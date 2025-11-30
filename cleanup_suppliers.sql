-- Fix typos in supplier names
UPDATE supplier SET supplier_name = 'Quacktastic Labs' WHERE supplier_name = 'Quaktastic Labs';
UPDATE supplier SET supplier_name = 'Baltic Bath Birds' WHERE supplier_name = 'Baltic Silicon Trading';

-- Delete duplicates, keeping the ones with intentional UUIDs (a1b2c3d4-... pattern)
-- First, let's see which ones to delete
DELETE FROM supplier WHERE supplier_id IN (
    '2c252448-6b39-4d9f-9469-1fda458c68ea',  -- duplicate Quacktastic Labs (typo version)
    'f85b8e8f-9e4f-455b-be41-f2428dfa54eb',  -- Baltic Silicon Trading (wrong name)
    '62032b50-21ef-481f-83b1-3a334bf60a03',  -- duplicate Baltic Bath Birds
    '7c90d07c-c8df-4ada-aebf-fee07f08346f',  -- duplicate Northbridge Embedded Systems
    'c8a0453d-c1c6-47fe-a004-504faf30e1ff',  -- duplicate Pacific Espresso Supply
    'a3a8c8ef-7a52-4b36-acc1-17f2c2dfe7d1',  -- duplicate Dunkler Premium Coffee Systems
    'e90dbbfa-6f1e-433a-a6d1-ed93f1653b5d',  -- Giuseppe's (not in simulator)
    'df9cba13-443f-484d-a636-ecae6729db5f'   -- Evergreen (not in simulator)
);

-- Verify the final list
SELECT supplier_id, supplier_name, supplier_email FROM supplier ORDER BY supplier_name;
