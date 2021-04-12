from os import getenv
from json import loads as json_loads
import random
import yaml
import signal
import time

from kubernetes import config, watch
from kubernetes.client import ApiClient, CoreV1Api, V1ObjectReference, V1ObjectMeta, V1Binding, Configuration
from kubernetes.client.rest import ApiException, RESTClientObject

from logging import basicConfig, getLogger, INFO

formatter = " %(asctime)s | %(levelname)-6s | %(process)d | %(threadName)-12s |" \
            " %(thread)-15d | %(name)-30s | %(filename)s:%(lineno)d | %(message)s |"
basicConfig(level=INFO, format=formatter)
logger = getLogger("meetup-scheduler")

V1_CLIENT = None  # type: CoreV1Api
SCHEDULE_STRATEGY = "schedulingStrategy=meetup"
_NOSCHEDULE_TAINT = "NoSchedule"
_RUNNING = True

def sigterm(x, y):
    global _RUNNING
    logger.info(f"SIGTERM received, time to leave")
    _RUNNING = False

signal.signal(signal.SIGTERM, sigterm)

scheduler_config = {}
with open(r'/config.yaml') as file:
    # The FullLoader parameter handles the conversion from YAML
    # scalar values to Python the dictionary format
    scheduler_config = yaml.load(file, Loader=yaml.FullLoader)

if scheduler_config is None: scheduler_config = {}
if 'rules' not in scheduler_config: scheduler_config['rules'] = {}

def _get_ready_nodes(v1_client, filtered=True):
    ready_nodes = []
    try:
        for n in v1_client.list_node().items:
            if n.metadata.labels.get("noCustomScheduler") == "yes":
                logger.info(f"Skipping Node {n.metadata.name} since it has noCustomScheduler label")
                continue
            if filtered:
                if not n.spec.unschedulable:
                    no_schedule_taint = False
                    if n.spec.taints:
                        for taint in n.spec.taints:
                            if _NOSCHEDULE_TAINT == taint.to_dict().get("effect", None):
                                no_schedule_taint = True
                                break
                    if not no_schedule_taint:
                        for status in n.status.conditions:
                            if status.status == "True" and status.type == "Ready" and n.metadata.name:
                                ready_nodes.append(n)
                    else:
                        logger.error("NoSchedule taint effect on node %s", n.metadata.name)
                else:
                    logger.error("Scheduling disabled on %s ", n.metadata.name)
            else:
                if n.metadata.name:
                    ready_nodes.append(n)
        logger.info("Nodes : %s, Filtered: %s", list(map(lambda n: n.metadata.name,ready_nodes)), filtered)
    except ApiException as e:
        logger.error(json_loads(e.body)["message"])
        ready_nodes = []
    return ready_nodes


def _get_schedulable_node(v1_client):
    node_list = _get_ready_nodes(v1_client)
    if not node_list:
        return None
    available_nodes = list(set(map(lambda n: n.metadata.name,node_list)))
    return random.choice(available_nodes)

def filter_to_first_matching_node(v1_client, pod_metadata):

    # get default
    node_list = _get_ready_nodes(v1_client)
    if not node_list:
        return None
    available_nodes = node_list
    selected = None

    any_opt_in = False

    logger.info("filter_to_first_matching_node[{}]".format(pod_metadata.name))

    for rule_name in scheduler_config['rules'].keys():
        rule = scheduler_config['rules'][rule_name]

        logger.info("Checking rule [{}]".format(rule_name))

        n_matches = node_list
        opt_in_to_match = False

        # filter by namespace
        if 'namespace' in rule:
            ns = rule['namespace']
            logger.info("Rule [{}] has namespace '{}'".format(rule_name,ns))
            if pod_metadata.namespace is not None:
                if pod_metadata.namespace == ns:
                    opt_in_to_match = True
                    logger.info("Pod matches namespace [{}]".format(ns))
                    logger.info("Pod n_matches before: {}".format(len(n_matches)))
                    n_matches = list(filter(lambda n: has_matching_label(n.metadata.labels, 'namespace', ns), n_matches))
                    logger.info("Pod n_matches after: {}".format(len(n_matches)))
                else:
                    logger.info("Pod namespace [{}] does not match rule".format(pod_metadata.namespace))
            else:
                logger.info("Pod does not contain namespace info!")

        # filter by labels (AND, not OR)
        if 'labels' in rule:
            labels = rule['labels']
            for key in labels.keys():
                value = labels[key]
                if has_matching_label(pod_metadata.labels,key,value):
                    opt_in_to_match = True
                    n_matches = list(filter(lambda n: has_matching_label(n.metadata.labels, key, value), n_matches))

        # if rule conditions were matched
        if opt_in_to_match:
            logger.info("Rule [{}] was opted into by pod [{}]".format(rule_name,pod_metadata.name))
            any_opt_in = True
            this_selected = random.choice(n_matches).metadata.name if len(n_matches) > 0 else None
            if this_selected is not None: # there might be other rules that match, if this has no nodes
                logger.info("Rule [{}] selected node [{}] for pod [{}]".format(rule_name,this_selected,pod_metadata.name))
                selected = this_selected # found one
                break
            else:
                logger.info("Rule [{}] did not select a node for pod [{}]".format(rule_name,pod_metadata.name))


    fin = selected if any_opt_in else random.choice(list(set(map(lambda n: n.metadata.name,available_nodes))))
    logger.info("filter_to_first_matching_node resulted in node [{}] for pod [{}]".format(fin,pod_metadata.name))
    return fin

def has_matching_label(labels, key, value):
    return key in labels.keys() and labels[key] == value

def schedule_pod(v1_client, name, node, namespace):
    target = V1ObjectReference()
    target.kind = "Node"
    target.apiVersion = "v1"
    target.name = node
    meta = V1ObjectMeta()
    meta.name = name
    body = V1Binding(api_version=None, kind=None, metadata=meta, target=target)
    logger.info("Binding Pod: %s  to  Node: %s", name, node)
    return v1_client.create_namespaced_pod_binding(name, namespace, body)

def watch_pod_events_cycle():
    global _RUNNING
    global V1_CLIENT
    try:
        logger.info("Checking for pod events....")
        try:
            watcher = watch.Watch()
            #for event in watcher.stream(V1_CLIENT.list_pod_for_all_namespaces, label_selector=SCHEDULE_STRATEGY, timeout_seconds=20):
            for event in watcher.stream(V1_CLIENT.list_pod_for_all_namespaces, timeout_seconds=20):
                if not _RUNNING: break
                logger.debug(f"Event: {event['type']} {event['object'].kind}, {event['object'].metadata.namespace}, {event['object'].metadata.name}, {event['object'].status.phase}")
                if event["object"].status.phase == "Pending":
                    try:
                        metadata = event["object"].metadata
                        logger.info(f'{metadata.name} needs scheduling...')
                        pod_namespace = metadata.namespace
                        pod_name = metadata.name
                        service_name = metadata.labels["serviceName"] if 'serviceName' in metadata.labels else None
                        logger.info("Processing for Pod: %s/%s", pod_namespace, pod_name)
                        node_name = filter_to_first_matching_node(V1_CLIENT, metadata)
                        if node_name:
                            logger.info("Namespace %s, PodName %s , Node Name: %s  Service Name: %s",
                                        pod_namespace, pod_name, node_name, service_name)
                            res = schedule_pod(V1_CLIENT, pod_name, node_name, pod_namespace)
                            logger.info("Response %s ", res)
                        else:
                            logger.error(f"Found no valid node to schedule {pod_name} in {pod_namespace}")
                    except ApiException as e:
                        logger.error(json_loads(e.body)["message"])
                    except ValueError as e:
                        logger.error("Value Error %s", e)
                    except:
                        logger.exception("Ignoring Exception")
            logger.debug("Resetting k8s watcher...")
        except kubernetes.client.exceptions.ApiException as err:
            s_err = "{}".format(err)
            if s_err.contains("Unauthorized") or s_err.contains("401"):
                logger.exception("Caught 401 API exception; exiting")
                _RUNNING = False
        except:
            logger.exception("Ignoring Exception")
        finally:
            del watcher
    except:
        logger.exception("Ignoring Exception & listening for pod events")


if __name__ == "__main__":
    config.load_incluster_config()
    V1_CLIENT = CoreV1Api()

    logger.info("Initializing the meetup scheduler...")
    logger.info("Watching for pod events...")
    time.sleep(5) # if launching this pod, avoid trying to schedlue itself
    while True:
        if _RUNNING:
            watch_pod_events_cycle()
        else:
            break
