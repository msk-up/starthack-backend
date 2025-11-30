CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS supplier (
    supplier_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    description TEXT NOT NULL,
    insights TEXT,
    image_url TEXT
);

CREATE TABLE IF NOT EXISTS agent (
    ng_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sys_promt TEXT NOT NULL,
    sup_id UUID NOT NULL REFERENCES supplier(supplier_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('negotiator', 'orchestrator'))
);

CREATE TABLE IF NOT EXISTS message (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ng_id UUID REFERENCES agent(ng_id) ON DELETE CASCADE, -- Made nullable for direct email storage
    supplier_id UUID REFERENCES supplier(supplier_id) ON DELETE CASCADE, -- Made nullable
    message_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    role TEXT NOT NULL, 
    message_text TEXT NOT NULL,
    metadata JSONB -- Store email specific headers like Subject, Sender
);

CREATE TABLE IF NOT EXISTS instructions (
    supplier_id UUID NOT NULL REFERENCES supplier(supplier_id) ON DELETE CASCADE,
    ng_id UUID NOT NULL REFERENCES agent(ng_id) ON DELETE CASCADE,
    instructions TEXT NOT NULL,
    PRIMARY KEY (supplier_id, ng_id)
);

CREATE TABLE IF NOT EXISTS product (
    product_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id UUID NOT NULL REFERENCES supplier(supplier_id) ON DELETE CASCADE,
    product_name TEXT NOT NULL
);

-- NEW TABLE FOR EMAIL CONFIGURATION
CREATE TABLE IF NOT EXISTS email_config (
    id SERIAL PRIMARY KEY,
    email_address TEXT NOT NULL UNIQUE,
    email_password TEXT NOT NULL, -- Note: In prod, encrypt this. For now, we store the App Password.
    imap_server TEXT DEFAULT 'imap.gmail.com',
    smtp_server TEXT DEFAULT 'smtp.gmail.com',
    is_active BOOLEAN DEFAULT TRUE
);