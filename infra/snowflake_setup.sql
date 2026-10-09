-- Run in a dedicated test account/database by an administrator, after resource approval.
-- Do not grant these roles account administration or access to reference/production data.
CREATE ROLE IF NOT EXISTS ELT_LOADER;
CREATE ROLE IF NOT EXISTS ELT_AGENT;
CREATE DATABASE IF NOT EXISTS ELT_SANDBOX;
CREATE WAREHOUSE IF NOT EXISTS ELT_TEST_WH
  WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
GRANT USAGE ON DATABASE ELT_SANDBOX TO ROLE ELT_LOADER;
GRANT CREATE SCHEMA ON DATABASE ELT_SANDBOX TO ROLE ELT_LOADER;
GRANT USAGE ON DATABASE ELT_SANDBOX TO ROLE ELT_AGENT;
GRANT USAGE ON WAREHOUSE ELT_TEST_WH TO ROLE ELT_LOADER;
GRANT USAGE ON WAREHOUSE ELT_TEST_WH TO ROLE ELT_AGENT;
-- Administrator: grant both roles directly to the dedicated SDK user.
-- Keep ELT_AGENT independent: do not grant ELT_LOADER or elevated roles to it.
-- Episode-created schemas get scoped grants automatically. No raw INSERT/UPDATE/DELETE.
