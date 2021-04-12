# kube-scheduler-by-meta

This is a reference project to help various kubernetes cluster deployments schedule
 workloads on specific nodes based on namespaces and labels.

This was an outcome of work to schedule NeoLoad dynamic infrastructure (controller
  and load generator pods) within a single cluster but on specific nodes/groups.

# Why?

Some cloud-based managed kubernetes services have built-in support for 'pinning'
 new workloads to specific node groups (such as AWS Fargate profiles namespace options).

However, some do not, certainly not stock Kubernetes off-the-shelf configurations.

# How to run these examples

***NOTE: You should not deploy this on production clusters until you are familiar with how
 this component works and prove that it is working as expected (in a non-prod cluster).***

1. (if you use a namespace other than 'neoload-infra')
  a. Customize the config.yaml and publish a custom Docker image to your own repository
  b. Customize the deployment.yaml to use your published custom image
2. Apply the deploiyment.yaml
3. Apply a label (default above is 'neoload-infra') to nodes/groups where you want pods with this namespace scheduled
4. Apply a workload where pods are in the above namespace
5. Tail the logs of by-meta-scheduler pod in the kube-system namespace to see the results

# What

## scheduler.py

This is the actual code that schedules new pods based on matches between the pod/template
 namespace and any nodes that have a 'namespace' label whose value matches that of the
 pod.

## config.yaml

One or more 'rules' that act as the glue between pod namespace values and node labels.
 Rather than assuming that matching values between these two kubernetes entities
 must mean that custom scheduling will occur, this configuration file deterministically
 declares that this should happen.

## Dockerfile

An example of a custom Docker image preconfigured to connect workloads to nodes. Though
 this is [published on Dockerhub](https://hub.docker.com/repository/docker/paulsbruce/kube-scheduler-by-meta), you should always know and control what is deployed in your own environments.

## deployment.yaml

An reference example of how to deploy this custom scheduler on your own Kubernetes cluster.
 Once deployed, the logs of the 'by-meta-scheduler' will show what is being processed and
 when something is scheduled through this custom component.
