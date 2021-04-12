FROM python:3.7
RUN pip install kubernetes PyYAML>=5
COPY scheduler.py /scheduler.py
COPY config.yaml /config.yaml
CMD python /scheduler.py
