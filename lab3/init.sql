CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    price NUMERIC(10, 2) NOT NULL
);

INSERT INTO items (name, price) VALUES
    ('Laptop', 1500.00),
    ('Mouse', 25.00),
    ('Keyboard', 60.00)
ON CONFLICT DO NOTHING;
