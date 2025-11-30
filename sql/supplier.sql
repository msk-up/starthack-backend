CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS supplier (
    supplier_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_name TEXT,
    supplier_email TEXT,
    description TEXT NOT NULL,
    insights TEXT,
    image_url TEXT
);

CREATE TABLE IF NOT EXISTS negotiation (
    ng_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product TEXT NOT NULL,
    strategy TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent (
    agent_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ng_id UUID NOT NULL REFERENCES negotiation(ng_id) ON DELETE CASCADE,
    sup_id UUID NOT NULL REFERENCES supplier(supplier_id) ON DELETE CASCADE,
    sys_prompt TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('negotiator', 'orchestrator')),
    UNIQUE (ng_id, sup_id)
);

CREATE TABLE IF NOT EXISTS message (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ng_id UUID REFERENCES agent(ng_id) ON DELETE CASCADE, -- Made nullable for direct email storage
    supplier_id UUID REFERENCES supplier(supplier_id) ON DELETE CASCADE, -- Made nullable
    message_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    role TEXT NOT NULL, 
    message_text TEXT NOT NULL,
    metadata JSONB, -- Store email specific headers like Subject, Sender
    completed BOOLEAN NOT NULL DEFAULT FALSE -- Whether this message completes the conversation
);

CREATE TABLE IF NOT EXISTS instructions (
    supplier_id UUID NOT NULL REFERENCES supplier(supplier_id) ON DELETE CASCADE,
    ng_id UUID NOT NULL REFERENCES negotiation(ng_id) ON DELETE CASCADE,
    instructions TEXT NOT NULL,
    PRIMARY KEY (supplier_id, ng_id)
);

CREATE TABLE IF NOT EXISTS product (
    product_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id UUID NOT NULL REFERENCES supplier(supplier_id) ON DELETE CASCADE,
    product_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orchestrator_activity (
    activity_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ng_id UUID NOT NULL REFERENCES negotiation(ng_id) ON DELETE CASCADE,
    supplier_id UUID REFERENCES supplier(supplier_id) ON DELETE CASCADE,
    activity_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    action TEXT NOT NULL,
    summary TEXT,
    details TEXT,
    completed BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS negotiation_summary (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ng_id UUID NOT NULL REFERENCES negotiation(ng_id) ON DELETE CASCADE,
    supplier_id UUID REFERENCES supplier(supplier_id) ON DELETE CASCADE,
    agent_id UUID REFERENCES agent(agent_id) ON DELETE SET NULL,
    summary_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ng_id, supplier_id)
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

-- MIGRATIONS: Add columns to existing tables if they don't exist
ALTER TABLE supplier ADD COLUMN IF NOT EXISTS supplier_email TEXT;
ALTER TABLE message ADD COLUMN IF NOT EXISTS completed BOOLEAN NOT NULL DEFAULT FALSE;

-- Clean up orphaned message/instruction rows to satisfy new foreign keys
DELETE FROM message m WHERE m.ng_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM negotiation n WHERE n.ng_id = m.ng_id
);
DELETE FROM instructions i WHERE i.ng_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM negotiation n WHERE n.ng_id = i.ng_id
);

-- Ensure message.ng_id references negotiation directly (so agent PK can change)
ALTER TABLE message DROP CONSTRAINT IF EXISTS message_ng_id_fkey;
ALTER TABLE message
    ADD CONSTRAINT message_ng_id_fkey
    FOREIGN KEY (ng_id) REFERENCES negotiation(ng_id) ON DELETE CASCADE;

-- Ensure instructions.ng_id references negotiation directly
ALTER TABLE instructions DROP CONSTRAINT IF EXISTS instructions_ng_id_fkey;
ALTER TABLE instructions
    ADD CONSTRAINT instructions_ng_id_fkey
    FOREIGN KEY (ng_id) REFERENCES negotiation(ng_id) ON DELETE CASCADE;

-- Ensure agent table supports multiple suppliers per negotiation
ALTER TABLE agent ADD COLUMN IF NOT EXISTS agent_id UUID;
ALTER TABLE agent ALTER COLUMN agent_id SET DEFAULT gen_random_uuid();
UPDATE agent SET agent_id = gen_random_uuid() WHERE agent_id IS NULL;

ALTER TABLE negotiation_summary DROP CONSTRAINT IF EXISTS negotiation_summary_agent_id_fkey;
ALTER TABLE agent DROP CONSTRAINT IF EXISTS agent_pkey;
ALTER TABLE agent ADD CONSTRAINT agent_pkey PRIMARY KEY (agent_id);
ALTER TABLE negotiation_summary
    ADD CONSTRAINT negotiation_summary_agent_id_fkey
    FOREIGN KEY (agent_id) REFERENCES agent(agent_id) ON DELETE SET NULL;

ALTER TABLE agent DROP CONSTRAINT IF EXISTS agent_ng_sup_unique;
ALTER TABLE agent ADD CONSTRAINT agent_ng_sup_unique UNIQUE (ng_id, sup_id);

-- Fix message role constraint to include 'supplier'
ALTER TABLE message DROP CONSTRAINT IF EXISTS message_role_check;
ALTER TABLE message ADD CONSTRAINT message_role_check CHECK (role IN ('negotiator', 'orchestrator', 'supplier'));
