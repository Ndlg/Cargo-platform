CREATE DATABASE IF NOT EXISTS cargo_platform
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'cargo_user'@'%' IDENTIFIED BY 'cargo_pass';
GRANT ALL PRIVILEGES ON cargo_platform.* TO 'cargo_user'@'%';

USE cargo_platform;

CREATE TABLE IF NOT EXISTS tenants (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(128) NOT NULL,
  code VARCHAR(64) NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  remark VARCHAR(512) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_tenants_code (code)
);

CREATE TABLE IF NOT EXISTS workspaces (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  name VARCHAR(128) NOT NULL,
  code VARCHAR(64) NOT NULL UNIQUE,
  remark VARCHAR(512) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_workspaces_tenant (tenant_id)
);

CREATE TABLE IF NOT EXISTS users (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL UNIQUE,
  display_name VARCHAR(128) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS roles (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  name VARCHAR(64) NOT NULL,
  remark VARCHAR(512) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_roles_tenant (tenant_id),
  INDEX idx_roles_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS user_workspaces (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  role_id BIGINT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_user_workspaces_tenant (tenant_id),
  INDEX idx_user_workspaces_workspace (workspace_id),
  UNIQUE KEY uk_user_workspace (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS collectors (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  collector_id VARCHAR(128) NOT NULL,
  collector_name VARCHAR(128) NOT NULL,
  token_hash VARCHAR(255) NULL,
  source_machine VARCHAR(128) NULL,
  client_version VARCHAR(64) NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  online_status VARCHAR(32) NOT NULL DEFAULT 'offline',
  last_heartbeat_at VARCHAR(64) NULL,
  status_payload JSON NULL,
  remark TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_collectors_tenant (tenant_id),
  INDEX idx_collectors_workspace (workspace_id),
  INDEX idx_collectors_token_hash (token_hash)
);

CREATE TABLE IF NOT EXISTS capture_tasks (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  name VARCHAR(128) NOT NULL,
  collector_id BIGINT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  started_at VARCHAR(64) NULL,
  ended_at VARCHAR(64) NULL,
  config JSON NULL,
  remark TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_capture_tasks_tenant (tenant_id),
  INDEX idx_capture_tasks_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS capture_batches (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  task_id BIGINT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  record_count INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_capture_batches_tenant (tenant_id),
  INDEX idx_capture_batches_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS raw_capture_records (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  capture_batch_id BIGINT NULL,
  task_id BIGINT NULL,
  document_id VARCHAR(128) NULL,
  collector_id BIGINT NULL,
  source_machine VARCHAR(128) NULL,
  source_component VARCHAR(128) NULL,
  source_index VARCHAR(128) NULL,
  dedupe_key VARCHAR(255) NULL,
  captured_at VARCHAR(64) NULL,
  waybill_mode VARCHAR(128) NULL,
  payload_format VARCHAR(32) NOT NULL DEFAULT 'unknown',
  raw_payload LONGTEXT NOT NULL,
  source_columns JSON NULL,
  parsed_payload JSON NULL,
  standard_detail_id BIGINT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_raw_capture_records_tenant (tenant_id),
  INDEX idx_raw_capture_records_workspace (workspace_id),
  INDEX idx_raw_capture_records_dedupe_key (dedupe_key)
);

CREATE TABLE IF NOT EXISTS standard_detail_batches (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  waybill_mode_id BIGINT NULL,
  source_type VARCHAR(32) NOT NULL,
  file_path VARCHAR(512) NULL,
  status VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_standard_detail_batches_tenant (tenant_id),
  INDEX idx_standard_detail_batches_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS standard_details (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  standard_detail_batch_id BIGINT NOT NULL,
  waybill_mode VARCHAR(128) NULL,
  full_text LONGTEXT NULL,
  field_values JSON NOT NULL,
  image_match_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  stall_match_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  raw_payload LONGTEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_standard_details_tenant (tenant_id),
  INDEX idx_standard_details_workspace_batch (workspace_id, standard_detail_batch_id)
);

CREATE TABLE IF NOT EXISTS export_header_definitions (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  name VARCHAR(128) NOT NULL,
  code VARCHAR(64) NOT NULL,
  data_type VARCHAR(32) NOT NULL DEFAULT 'text',
  export_enabled TINYINT(1) NOT NULL DEFAULT 1,
  export_order INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_export_header_definitions_tenant (tenant_id),
  UNIQUE KEY uk_export_header_definitions_workspace_code (workspace_id, code)
);

CREATE TABLE IF NOT EXISTS products (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  name VARCHAR(128) NOT NULL,
  code VARCHAR(64) NULL,
  keywords JSON NULL,
  stall_id BIGINT NULL,
  remark TEXT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  UNIQUE KEY uk_products_workspace_name (workspace_id, name),
  INDEX idx_products_tenant (tenant_id),
  INDEX idx_products_workspace (workspace_id),
  INDEX idx_products_stall (stall_id)
);

CREATE TABLE IF NOT EXISTS product_skus (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  name VARCHAR(128) NOT NULL,
  code VARCHAR(64) NULL,
  keywords JSON NULL,
  stall_id BIGINT NULL,
  image_asset_id BIGINT NULL,
  sort_order INT NOT NULL DEFAULT 100,
  remark TEXT NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  UNIQUE KEY uk_product_skus_product_name (workspace_id, product_id, name),
  INDEX idx_product_skus_tenant (tenant_id),
  INDEX idx_product_skus_workspace_product (workspace_id, product_id),
  INDEX idx_product_skus_stall (stall_id)
);

CREATE TABLE IF NOT EXISTS stalls (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  name VARCHAR(128) NOT NULL,
  contact_name VARCHAR(128) NULL,
  remark VARCHAR(512) NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_stalls_tenant (tenant_id),
  INDEX idx_stalls_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS image_assets (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  name VARCHAR(128) NOT NULL,
  file_path VARCHAR(512) NOT NULL,
  content_hash VARCHAR(128) NULL,
  mime_type VARCHAR(64) NULL,
  file_size BIGINT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_image_assets_tenant (tenant_id),
  INDEX idx_image_assets_workspace (workspace_id)
);
