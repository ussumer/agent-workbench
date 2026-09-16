-- Catalog and inventory schema for the procurement mini-ERP.
-- Idempotent: it is applied on every start, while data seeding only happens on an
-- empty database (see SeedLoader). Nothing here truncates existing rows.

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id VARCHAR(16)  PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    active      BOOLEAN      NOT NULL,
    contact     VARCHAR(128) NOT NULL
);

CREATE TABLE IF NOT EXISTS parts (
    part_id VARCHAR(16)  PRIMARY KEY,
    sku     VARCHAR(64)  NOT NULL,
    name    VARCHAR(128) NOT NULL,
    unit    VARCHAR(16)  NOT NULL,
    active  BOOLEAN      NOT NULL,
    CONSTRAINT uk_parts_sku UNIQUE (sku)
);

CREATE TABLE IF NOT EXISTS supplier_parts (
    supplier_id   VARCHAR(16)   NOT NULL,
    part_id       VARCHAR(16)   NOT NULL,
    catalog_price NUMERIC(12, 2) NOT NULL,
    currency      VARCHAR(8)    NOT NULL,
    lead_days     INTEGER       NOT NULL,
    CONSTRAINT pk_supplier_parts PRIMARY KEY (supplier_id, part_id),
    CONSTRAINT fk_supplier_parts_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers (supplier_id),
    CONSTRAINT fk_supplier_parts_part FOREIGN KEY (part_id) REFERENCES parts (part_id),
    CONSTRAINT ck_supplier_parts_price CHECK (catalog_price > 0),
    CONSTRAINT ck_supplier_parts_currency CHECK (currency = 'CNY'),
    CONSTRAINT ck_supplier_parts_lead_days CHECK (lead_days >= 0)
);

CREATE TABLE IF NOT EXISTS inventory (
    part_id           VARCHAR(16) PRIMARY KEY,
    on_hand           INTEGER     NOT NULL,
    warning_threshold INTEGER     NOT NULL,
    target_stock      INTEGER     NOT NULL,
    CONSTRAINT fk_inventory_part FOREIGN KEY (part_id) REFERENCES parts (part_id),
    CONSTRAINT ck_inventory_on_hand CHECK (on_hand >= 0),
    CONSTRAINT ck_inventory_threshold CHECK (warning_threshold >= 0),
    CONSTRAINT ck_inventory_target CHECK (target_stock >= warning_threshold)
);

CREATE INDEX IF NOT EXISTS idx_supplier_parts_part ON supplier_parts (part_id);
CREATE INDEX IF NOT EXISTS idx_parts_name ON parts (name);
CREATE INDEX IF NOT EXISTS idx_suppliers_name ON suppliers (name);
CREATE INDEX IF NOT EXISTS idx_inventory_threshold ON inventory (warning_threshold);
