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

CREATE TABLE IF NOT EXISTS waybill_modes (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(128) NOT NULL,
  code VARCHAR(64) NOT NULL,
  input_format VARCHAR(32) NOT NULL,
  remark VARCHAR(512) NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  UNIQUE KEY uk_waybill_mode_code (code)
);

CREATE TABLE IF NOT EXISTS waybill_templates (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  waybill_mode_id BIGINT NOT NULL,
  name VARCHAR(128) NOT NULL,
  input_format VARCHAR(32) NOT NULL,
  recognition_rules JSON NULL,
  extraction_rules JSON NULL,
  version VARCHAR(32) NOT NULL DEFAULT '1',
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_waybill_templates_mode (waybill_mode_id)
);

CREATE TABLE IF NOT EXISTS waybill_template_fields (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  waybill_template_id BIGINT NOT NULL,
  target_field_code VARCHAR(64) NOT NULL,
  extraction_type VARCHAR(32) NOT NULL,
  extraction_config JSON NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_waybill_template_fields_template (waybill_template_id)
);

CREATE TABLE IF NOT EXISTS print_template_configs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  waybill_mode VARCHAR(128) NOT NULL,
  template_key VARCHAR(255) NOT NULL,
  template_label VARCHAR(128) NULL,
  template_source VARCHAR(64) NULL,
  parse_status VARCHAR(32) NOT NULL DEFAULT 'platform_structured',
  config JSON NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  remark TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_print_template_configs_tenant (tenant_id),
  INDEX idx_print_template_configs_workspace (workspace_id),
  UNIQUE KEY uk_print_template_workspace_key (workspace_id, waybill_mode, template_key)
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

CREATE TABLE IF NOT EXISTS field_definitions (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  waybill_mode_id BIGINT NULL,
  name VARCHAR(128) NOT NULL,
  code VARCHAR(64) NOT NULL,
  data_type VARCHAR(32) NOT NULL DEFAULT 'text',
  is_visible TINYINT(1) NOT NULL DEFAULT 1,
  is_filterable TINYINT(1) NOT NULL DEFAULT 1,
  use_for_matching TINYINT(1) NOT NULL DEFAULT 0,
  use_for_image_match TINYINT(1) NOT NULL DEFAULT 0,
  use_for_stall_match TINYINT(1) NOT NULL DEFAULT 0,
  use_for_grouping TINYINT(1) NOT NULL DEFAULT 0,
  use_as_quantity TINYINT(1) NOT NULL DEFAULT 0,
  use_as_remark TINYINT(1) NOT NULL DEFAULT 0,
  export_enabled TINYINT(1) NOT NULL DEFAULT 1,
  export_order INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_field_definitions_tenant (tenant_id),
  UNIQUE KEY uk_field_definitions_workspace_code (workspace_id, code)
);

CREATE TABLE IF NOT EXISTS field_role_configs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  waybill_mode_id BIGINT NULL,
  field_definition_id BIGINT NOT NULL,
  role_code VARCHAR(64) NOT NULL,
  config JSON NULL,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_field_role_configs_tenant (tenant_id),
  INDEX idx_field_role_configs_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS key_field_sets (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  waybill_mode_id BIGINT NULL,
  name VARCHAR(128) NOT NULL,
  purpose VARCHAR(32) NOT NULL,
  field_codes JSON NOT NULL,
  priority INT NOT NULL DEFAULT 100,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_key_field_sets_tenant (tenant_id),
  INDEX idx_key_field_sets_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS match_rules (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  waybill_mode_id BIGINT NULL,
  key_field_set_id BIGINT NOT NULL,
  match_values JSON NOT NULL,
  target_type VARCHAR(32) NOT NULL,
  target_id BIGINT NULL,
  target_name VARCHAR(128) NULL,
  priority INT NOT NULL DEFAULT 100,
  is_enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_match_rules_tenant (tenant_id),
  INDEX idx_match_rules_workspace_target (workspace_id, target_type)
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

CREATE TABLE IF NOT EXISTS report_batches (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  standard_detail_batch_id BIGINT NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_report_batches_tenant (tenant_id),
  INDEX idx_report_batches_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS report_lines (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  report_batch_id BIGINT NOT NULL,
  field_values JSON NOT NULL,
  quantity VARCHAR(64) NULL,
  image_asset_id BIGINT NULL,
  stall_id BIGINT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_report_lines_tenant (tenant_id),
  INDEX idx_report_lines_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS exception_records (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  source_type VARCHAR(64) NOT NULL,
  source_id BIGINT NULL,
  exception_type VARCHAR(64) NOT NULL,
  message VARCHAR(512) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'open',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_exception_records_tenant (tenant_id),
  INDEX idx_exception_records_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS export_records (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  report_batch_id BIGINT NULL,
  export_mode VARCHAR(32) NOT NULL,
  file_path VARCHAR(512) NULL,
  status VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_export_records_tenant (tenant_id),
  INDEX idx_export_records_workspace (workspace_id)
);

CREATE TABLE IF NOT EXISTS operation_logs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_id BIGINT NULL,
  workspace_id BIGINT NOT NULL,
  user_id BIGINT NULL,
  action VARCHAR(128) NOT NULL,
  target_type VARCHAR(64) NULL,
  target_id BIGINT NULL,
  detail JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_by BIGINT NULL,
  updated_by BIGINT NULL,
  is_deleted TINYINT(1) NOT NULL DEFAULT 0,
  INDEX idx_operation_logs_tenant (tenant_id),
  INDEX idx_operation_logs_workspace (workspace_id)
);
