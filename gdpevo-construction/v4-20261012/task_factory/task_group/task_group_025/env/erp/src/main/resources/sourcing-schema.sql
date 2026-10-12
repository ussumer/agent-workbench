CREATE TABLE IF NOT EXISTS sourcing_records (
 domain VARCHAR(32) NOT NULL,
 record_id VARCHAR(120) NOT NULL,
 program_id VARCHAR(40),
 supplier_id VARCHAR(40),
 payload CLOB NOT NULL,
 PRIMARY KEY(domain, record_id)
);
CREATE INDEX IF NOT EXISTS sourcing_program_domain ON sourcing_records(domain, program_id, record_id);
CREATE INDEX IF NOT EXISTS sourcing_supplier_domain ON sourcing_records(domain, supplier_id, record_id);
CREATE TABLE IF NOT EXISTS sourcing_seed (seed_id BIGINT PRIMARY KEY, sha256 VARCHAR(64) NOT NULL);
