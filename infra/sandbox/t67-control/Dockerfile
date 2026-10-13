FROM __BASE_IMAGE__
COPY dependencies/ /opt/server-deps/
COPY snapshot.json /opt/server-snapshot.json
ENV PYTHONPATH=/opt/server-deps
LABEL t67.source_snapshot_sha256=__SOURCE_SHA__ t67.server_version=__SERVER_VERSION__
ENTRYPOINT ["/usr/local/bin/python", "-m", "opensandbox_server.cli"]
