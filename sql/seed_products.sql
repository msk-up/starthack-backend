-- Seed products table with appropriate products for each supplier category
-- Clear existing products first to avoid duplicates
DELETE FROM product;

-- Add supplier_name column if it doesn't exist
ALTER TABLE product ADD COLUMN IF NOT EXISTS supplier_name TEXT;

-- Rubber Duck suppliers
INSERT INTO product (supplier_id, product_name, supplier_name)
SELECT supplier_id, 'Rubber Ducks', supplier_name
FROM supplier
WHERE supplier_name IN (
    'Quacktastic Labs',
    'Baltic Bath Birds',
    'Golden Float Toys',
    'Squeaky Clean Co',
    'Duckworth Industrial',
    'Lucky Duck Emporium'
);

-- Semiconductor suppliers
INSERT INTO product (supplier_id, product_name, supplier_name)
SELECT supplier_id, 'nRF54L15', supplier_name
FROM supplier
WHERE supplier_name IN (
    'Northbridge Embedded Systems',
    'Shenzhen MicroTech',
    'Silicon Valley Components',
    'EuroLink Semiconductors'
);

-- Coffee Equipment suppliers
INSERT INTO product (supplier_id, product_name, supplier_name)
SELECT supplier_id, 'Coffee Equipment', supplier_name
FROM supplier
WHERE supplier_name IN (
    'Dunkler Premium Coffee Systems',
    'Pacific Espresso Supply',
    'Bean Machine Wholesale',
    'Tokyo Coffee Equipment'
);

-- Verify
SELECT product_id, product_name, supplier_name FROM product ORDER BY product_name, supplier_name;
