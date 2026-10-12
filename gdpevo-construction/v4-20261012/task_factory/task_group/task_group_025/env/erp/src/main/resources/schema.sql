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

-- Order state. There is deliberately no pending/approved order status: approval happens before the
-- write reaches the ERP, so an unapproved request must leave no order behind at all.
CREATE SEQUENCE IF NOT EXISTS order_number_seq START WITH 1 INCREMENT BY 1;

CREATE TABLE IF NOT EXISTS orders (
    order_id      VARCHAR(32)    PRIMARY KEY,
    order_no      VARCHAR(32)    NOT NULL,
    owner_user_id VARCHAR(64)    NOT NULL,
    supplier_id   VARCHAR(16)    NOT NULL,
    currency      VARCHAR(8)     NOT NULL,
    total_amount  NUMERIC(14, 2) NOT NULL,
    version       INTEGER        NOT NULL,
    note          VARCHAR(512),
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at    TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT uk_orders_order_no UNIQUE (order_no),
    CONSTRAINT fk_orders_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers (supplier_id),
    CONSTRAINT ck_orders_version CHECK (version >= 1),
    CONSTRAINT ck_orders_currency CHECK (currency = 'CNY'),
    CONSTRAINT ck_orders_total CHECK (total_amount > 0)
);

CREATE TABLE IF NOT EXISTS order_lines (
    order_id    VARCHAR(32)    NOT NULL,
    line_no     INTEGER        NOT NULL,
    part_id     VARCHAR(16)    NOT NULL,
    quantity    INTEGER        NOT NULL,
    unit_price  NUMERIC(12, 2) NOT NULL,
    line_amount NUMERIC(14, 2) NOT NULL,
    CONSTRAINT pk_order_lines PRIMARY KEY (order_id, line_no),
    CONSTRAINT fk_order_lines_order FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT fk_order_lines_part FOREIGN KEY (part_id) REFERENCES parts (part_id),
    CONSTRAINT uk_order_lines_part UNIQUE (order_id, part_id),
    CONSTRAINT ck_order_lines_quantity CHECK (quantity BETWEEN 1 AND 10000),
    CONSTRAINT ck_order_lines_price CHECK (unit_price > 0)
);

-- Idempotency ledger. The unique key is (owner, operation_id); the frozen payload hash and the
-- target resource are stored alongside the successful response so a retry can be answered without
-- repeating the business write.
CREATE TABLE IF NOT EXISTS operations (
    owner_user_id VARCHAR(64)  NOT NULL,
    operation_id  VARCHAR(128) NOT NULL,
    payload_hash  CHAR(64)     NOT NULL,
    operation_type VARCHAR(32) NOT NULL,
    resource_id   VARCHAR(64),
    response_json CLOB         NOT NULL,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_operations PRIMARY KEY (owner_user_id, operation_id)
);

CREATE INDEX IF NOT EXISTS idx_supplier_parts_part ON supplier_parts (part_id);
CREATE INDEX IF NOT EXISTS idx_parts_name ON parts (name);
CREATE INDEX IF NOT EXISTS idx_suppliers_name ON suppliers (name);
CREATE INDEX IF NOT EXISTS idx_inventory_threshold ON inventory (warning_threshold);
CREATE INDEX IF NOT EXISTS idx_orders_owner ON orders (owner_user_id, created_at DESC, order_id DESC);
CREATE INDEX IF NOT EXISTS idx_orders_supplier ON orders (supplier_id);
CREATE INDEX IF NOT EXISTS idx_order_lines_order ON order_lines (order_id);
