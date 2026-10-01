-- Sample orders for the refund workflow. Portable across PostgreSQL and SQLite.
CREATE TABLE IF NOT EXISTS orders (
    id             TEXT PRIMARY KEY,
    customer_email TEXT NOT NULL,
    amount         NUMERIC(10, 2) NOT NULL,
    currency       TEXT NOT NULL DEFAULT 'USD',
    status         TEXT NOT NULL,
    delivered_at   TEXT,
    refund_status  TEXT NOT NULL DEFAULT 'none',
    refund_id      TEXT
);

INSERT INTO orders (id, customer_email, amount, currency, status, delivered_at, refund_status) VALUES
    ('A1001', 'mia.chen@example.com',    120.00, 'USD', 'delivered', '2026-09-24', 'none'),
    ('A1002', 'leo.martin@example.com',  899.00, 'USD', 'delivered', '2026-09-21', 'none'),
    ('A1003', 'ava.patel@example.com',    60.00, 'USD', 'delivered', '2026-09-27', 'none'),
    ('A1004', 'noah.kim@example.com',     45.00, 'USD', 'delivered', '2026-09-25', 'none'),
    ('A1005', 'emma.silva@example.com',   75.00, 'USD', 'delivered', '2026-09-22', 'none'),
    ('A1006', 'liam.wong@example.com',  1500.00, 'USD', 'shipped',   NULL,         'none'),
    ('A1007', 'zoe.garcia@example.com',  210.00, 'USD', 'delivered', '2026-09-18', 'refunded'),
    ('A1008', 'ethan.ito@example.com',   640.00, 'USD', 'delivered', '2026-09-26', 'none'),
    ('A1009', 'chloe.novak@example.com',  89.90, 'USD', 'delivered', '2026-09-28', 'none'),
    ('A1010', 'omar.haddad@example.com', 499.00, 'USD', 'delivered', '2026-09-23', 'none'),
    ('A1011', 'ivy.larsen@example.com',  501.00, 'USD', 'delivered', '2026-09-23', 'none'),
    ('A1012', 'sam.okafor@example.com',   35.50, 'USD', 'delivered', '2026-09-29', 'none')
ON CONFLICT (id) DO NOTHING;
