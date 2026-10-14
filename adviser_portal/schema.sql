-- Draft application schema for a DEDICATED CLIENT database only.
-- DO NOT apply to history_stage / public demo. Auth and app-runtime roles
-- must be separately provisioned before this migration can go live.
-- PostgreSQL 17 / pgcrypto UUIDs. Fail-closed row-level security.
CREATE SCHEMA IF NOT EXISTS adviser_private;

CREATE TABLE IF NOT EXISTS adviser_private.organizations(
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name text NOT NULL CHECK(length(display_name) BETWEEN 1 AND 120),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS adviser_private.members(
  org_id uuid NOT NULL REFERENCES adviser_private.organizations(id),
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  identity_subject text NOT NULL,
  role text NOT NULL CHECK (role IN ('admin','adviser','client')),
  display_name text NOT NULL DEFAULT '',
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(org_id,id),
  UNIQUE(identity_subject)
);

CREATE TABLE IF NOT EXISTS adviser_private.clients(
  org_id uuid NOT NULL REFERENCES adviser_private.organizations(id),
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  advisor_member_id uuid,
  owner_member_id uuid,
  display_name text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(org_id,id),
  FOREIGN KEY(org_id, advisor_member_id) REFERENCES adviser_private.members(org_id,id),
  FOREIGN KEY(org_id, owner_member_id) REFERENCES adviser_private.members(org_id,id)
);
CREATE INDEX IF NOT EXISTS idx_clients_adviser ON adviser_private.clients(org_id,advisor_member_id);

CREATE TABLE IF NOT EXISTS adviser_private.client_positions(
  org_id uuid NOT NULL,
  client_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  instrument_key text NOT NULL CHECK (length(instrument_key) <= 160),
  display_name text NOT NULL CHECK(length(display_name) <= 240),
  units numeric(22,6) NOT NULL CHECK(units >= 0),
  reference_price numeric(22,6) CHECK(reference_price >= 0),
  currency text NOT NULL DEFAULT 'TWD' CHECK(length(currency) BETWEEN 3 AND 5),
  reference_date date,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(org_id,client_id,id),
  FOREIGN KEY(org_id,client_id) REFERENCES adviser_private.clients(org_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS adviser_private.client_risk_assessments(
  org_id uuid NOT NULL,
  client_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  answers jsonb NOT NULL CHECK(jsonb_typeof(answers) = 'object'),
  rr_level integer NOT NULL CHECK(rr_level BETWEEN 1 AND 5),
  methodology_version text NOT NULL CHECK(length(methodology_version) <= 40),
  assessor_member_id uuid NOT NULL,
  signed_off boolean NOT NULL DEFAULT false,
  assessed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(org_id,client_id,id),
  FOREIGN KEY(org_id,client_id) REFERENCES adviser_private.clients(org_id,id) ON DELETE CASCADE,
  FOREIGN KEY(org_id,assessor_member_id) REFERENCES adviser_private.members(org_id,id)
);

CREATE TABLE IF NOT EXISTS adviser_private.client_allocations(
  org_id uuid NOT NULL,
  client_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  strategy text NOT NULL CHECK(strategy IN ('gradual','rebalance')),
  inputs jsonb NOT NULL CHECK(jsonb_typeof(inputs)='object'),
  result jsonb NOT NULL CHECK(jsonb_typeof(result)='object'),
  approved boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(org_id,client_id,id),
  FOREIGN KEY(org_id,client_id) REFERENCES adviser_private.clients(org_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS adviser_private.audit_log(
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id uuid NOT NULL,
  client_id uuid,
  actor_member_id uuid NOT NULL,
  event_type text NOT NULL,
  event_at timestamptz NOT NULL DEFAULT now(),
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY(org_id,actor_member_id) REFERENCES adviser_private.members(org_id,id),
  FOREIGN KEY(org_id,client_id) REFERENCES adviser_private.clients(org_id,id)
);

-- Runtime identity must be assigned exclusively by the authenticated server
-- within one explicit transaction (SET LOCAL), never from raw browser JSON.
CREATE OR REPLACE FUNCTION adviser_private.request_org() RETURNS uuid
  LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.org_id',true),'')::uuid $$;
CREATE OR REPLACE FUNCTION adviser_private.request_member() RETURNS uuid
  LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.member_id',true),'')::uuid $$;
CREATE OR REPLACE FUNCTION adviser_private.request_role() RETURNS text
  LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.role',true),'') $$;

-- Owners and BYPASSRLS roles must NEVER be used by application requests.
ALTER TABLE adviser_private.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.organizations FORCE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.members ENABLE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.members FORCE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.clients FORCE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.client_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.client_positions FORCE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.client_risk_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.client_risk_assessments FORCE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.client_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.client_allocations FORCE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE adviser_private.audit_log FORCE ROW LEVEL SECURITY;

CREATE POLICY organizations_read ON adviser_private.organizations
  FOR SELECT USING(id = adviser_private.request_org());

CREATE POLICY members_read ON adviser_private.members
  FOR SELECT USING(org_id=adviser_private.request_org()
    AND (id=adviser_private.request_member()
      OR adviser_private.request_role()='admin'));

-- Separate read/write policies. A customer can NEVER update/delete client rows
-- merely because they are allowed to view their own information.
CREATE POLICY clients_read ON adviser_private.clients
  FOR SELECT USING(org_id=adviser_private.request_org()
    AND (adviser_private.request_role()='admin'
      OR (adviser_private.request_role()='adviser' AND advisor_member_id=adviser_private.request_member())
      OR (adviser_private.request_role()='client' AND owner_member_id=adviser_private.request_member())));
CREATE POLICY clients_add ON adviser_private.clients
  FOR INSERT WITH CHECK(org_id=adviser_private.request_org()
    AND (adviser_private.request_role()='admin'
      OR (adviser_private.request_role()='adviser' AND advisor_member_id=adviser_private.request_member())));
CREATE POLICY clients_modify ON adviser_private.clients
  FOR UPDATE USING(org_id=adviser_private.request_org()
    AND (adviser_private.request_role()='admin'
      OR (adviser_private.request_role()='adviser' AND advisor_member_id=adviser_private.request_member())))
  WITH CHECK(org_id=adviser_private.request_org()
    AND (adviser_private.request_role()='admin'
      OR (adviser_private.request_role()='adviser' AND advisor_member_id=adviser_private.request_member())));

-- Children are visible ONLY through the parent's RLS-checked client row.
CREATE POLICY positions_read ON adviser_private.client_positions
  FOR SELECT USING(org_id=adviser_private.request_org()
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_positions.org_id AND c.id=client_positions.client_id));
CREATE POLICY positions_add ON adviser_private.client_positions
  FOR INSERT WITH CHECK(org_id=adviser_private.request_org()
    AND adviser_private.request_role() IN ('adviser','admin')
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_positions.org_id AND c.id=client_positions.client_id));
CREATE POLICY positions_modify ON adviser_private.client_positions
  FOR UPDATE USING(org_id=adviser_private.request_org()
    AND adviser_private.request_role() IN ('adviser','admin')
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_positions.org_id AND c.id=client_positions.client_id))
  WITH CHECK(org_id=adviser_private.request_org()
    AND adviser_private.request_role() IN ('adviser','admin')
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_positions.org_id AND c.id=client_positions.client_id));

CREATE POLICY risk_read ON adviser_private.client_risk_assessments
  FOR SELECT USING(org_id=adviser_private.request_org()
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_risk_assessments.org_id AND c.id=client_risk_assessments.client_id));
CREATE POLICY risk_add ON adviser_private.client_risk_assessments
  FOR INSERT WITH CHECK(org_id=adviser_private.request_org()
    AND adviser_private.request_role() IN ('adviser','admin')
    AND assessor_member_id=adviser_private.request_member()
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_risk_assessments.org_id AND c.id=client_risk_assessments.client_id));
CREATE POLICY risk_modify ON adviser_private.client_risk_assessments
  FOR UPDATE USING(org_id=adviser_private.request_org()
    AND adviser_private.request_role() IN ('adviser','admin')
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_risk_assessments.org_id AND c.id=client_risk_assessments.client_id))
  WITH CHECK(org_id=adviser_private.request_org()
    AND adviser_private.request_role() IN ('adviser','admin')
    AND assessor_member_id=adviser_private.request_member()
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_risk_assessments.org_id AND c.id=client_risk_assessments.client_id));

CREATE POLICY allocations_read ON adviser_private.client_allocations
  FOR SELECT USING(org_id=adviser_private.request_org()
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_allocations.org_id AND c.id=client_allocations.client_id));
CREATE POLICY allocations_add ON adviser_private.client_allocations
  FOR INSERT WITH CHECK(org_id=adviser_private.request_org()
    AND adviser_private.request_role() IN ('adviser','admin')
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_allocations.org_id AND c.id=client_allocations.client_id));
CREATE POLICY allocations_modify ON adviser_private.client_allocations
  FOR UPDATE USING(org_id=adviser_private.request_org()
    AND adviser_private.request_role() IN ('adviser','admin')
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_allocations.org_id AND c.id=client_allocations.client_id))
  WITH CHECK(org_id=adviser_private.request_org()
    AND adviser_private.request_role() IN ('adviser','admin')
    AND EXISTS(SELECT 1 FROM adviser_private.clients c
      WHERE c.org_id=client_allocations.org_id AND c.id=client_allocations.client_id));

CREATE POLICY audit_read_admin ON adviser_private.audit_log
  FOR SELECT USING(org_id=adviser_private.request_org()
    AND adviser_private.request_role()='admin');
-- Audit insert must originate in audited server transactions and must match
-- authenticated member; clients cannot write audit rows.
CREATE POLICY audit_write ON adviser_private.audit_log
  FOR INSERT WITH CHECK(org_id=adviser_private.request_org()
    AND actor_member_id=adviser_private.request_member()
    AND adviser_private.request_role() IN ('admin','adviser'));

-- Security gate before production:
-- 1. Dedicated DB and non-owner, no-BYPASSRLS limited app role.
-- 2. Server verifies IdP identity and organisation membership, never accepts
--    org_id/role/member_id from the client.
-- 3. Live tests with two orgs, two advisers and multiple clients confirming
--    cross-tenant/cross-adviser reads/writes all fail, including joined tables.
-- 4. Fail-closed if SET LOCAL context is missing; secure session expiration,
--    CSRF mitigation, account revocation, and audit log provenance.
-- 5. Encrypted backups + restore drills, privacy terms, client consent.
