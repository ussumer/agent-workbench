# Prepared with the copied SHA-locked Maven 3.9.9 wrapper by build.sh.
FROM eclipse-temurin:21.0.8_9-jre-jammy@sha256:db1689535962d757a5adabf57387584ed543d38c0b9d1fe870123ea362ad73b0
WORKDIR /opt/sourcing
RUN mkdir -p /data && chown 10001:10001 /data
COPY erp/target/procurement-erp-0.1.0.jar app.jar
COPY seed.json seed.json
COPY fixtures/seed-v1.json catalog-seed.json
ENV ERP_DB_PATH=/data/sourcing ERP_SEED_PATH=/opt/sourcing/catalog-seed.json SOURCING_SEED_PATH=/opt/sourcing/seed.json
USER 10001:10001
EXPOSE 9025
ENTRYPOINT ["java","-jar","/opt/sourcing/app.jar"]
