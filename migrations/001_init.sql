-- Jobbot v2 initial MySQL schema.
-- Run with: python scripts/migrate.py
-- The application also calls SQLAlchemy create_all() on first start; this file is
-- the explicit production migration for managed MySQL instances.

CREATE TABLE IF NOT EXISTS resumes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(240) NOT NULL,
  original_name VARCHAR(240) NOT NULL,
  path VARCHAR(1024) NOT NULL,
  sha256 CHAR(64) NOT NULL,
  size_bytes INT NOT NULL DEFAULT 0,
  text_content LONGTEXT NULL,
  is_default TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  UNIQUE KEY uq_resumes_sha256 (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS jobs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  platform VARCHAR(40) NOT NULL,
  platform_job_id VARCHAR(160) NULL,
  company VARCHAR(240) NOT NULL DEFAULT '',
  company_slug VARCHAR(160) NULL,
  title VARCHAR(300) NOT NULL DEFAULT '',
  location VARCHAR(300) NULL,
  remote TINYINT(1) NOT NULL DEFAULT 0,
  salary VARCHAR(160) NULL,
  experience VARCHAR(160) NULL,
  description LONGTEXT NULL,
  url VARCHAR(512) NOT NULL,
  apply_url VARCHAR(1024) NULL,
  fingerprint CHAR(64) NOT NULL,
  posted_at DATETIME NULL,
  fetched_at DATETIME NOT NULL,
  score INT NULL,
  score_reason VARCHAR(1000) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'new',
  status_reason VARCHAR(1000) NULL,
  worker VARCHAR(80) NULL,
  claimed_at DATETIME NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_jobs_platform_job (platform, platform_job_id),
  UNIQUE KEY uq_jobs_url (url),
  KEY ix_jobs_platform (platform),
  KEY ix_jobs_fingerprint (fingerprint),
  KEY ix_jobs_status (status),
  KEY ix_jobs_status_platform (status, platform)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS applications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  job_id INT NOT NULL,
  status VARCHAR(48) NOT NULL,
  applied_at DATETIME NULL,
  week_start DATE NULL,
  fingerprint CHAR(64) NULL,
  resume_id INT NULL,
  resume_name VARCHAR(240) NULL,
  cover_letter_path VARCHAR(1024) NULL,
  answers_json LONGTEXT NULL,
  screenshot VARCHAR(1024) NULL,
  confirmation VARCHAR(1000) NULL,
  failure_reason VARCHAR(1000) NULL,
  next_action VARCHAR(300) NULL,
  tab_id VARCHAR(80) NULL,
  reference_id VARCHAR(160) NULL,
  dry_run TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_applications_job (job_id),
  KEY ix_applications_status (status),
  KEY ix_applications_week (week_start),
  KEY ix_applications_fingerprint (fingerprint),
  CONSTRAINT fk_applications_job FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  CONSTRAINT fk_applications_resume FOREIGN KEY (resume_id) REFERENCES resumes(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS submission_reservations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  job_id INT NOT NULL,
  fingerprint CHAR(64) NOT NULL,
  week_start DATE NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'reserved',
  tab_id VARCHAR(80) NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_reservations_job (job_id),
  UNIQUE KEY uq_reservations_fingerprint (fingerprint),
  KEY ix_reservations_status (status),
  KEY ix_reservations_week (week_start),
  CONSTRAINT fk_reservations_job FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS quota_weeks (
  week_start DATE PRIMARY KEY,
  cap INT NOT NULL DEFAULT 300,
  used INT NOT NULL DEFAULT 0,
  reserved INT NOT NULL DEFAULT 0,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS candidate_profile (
  id INT PRIMARY KEY DEFAULT 1,
  data JSON NOT NULL,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS settings (
  `key` VARCHAR(120) PRIMARY KEY,
  value JSON NOT NULL,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS activity_log (
  id INT AUTO_INCREMENT PRIMARY KEY,
  ts DATETIME NOT NULL,
  level VARCHAR(16) NOT NULL DEFAULT 'info',
  tab_id VARCHAR(80) NULL,
  platform VARCHAR(40) NULL,
  action VARCHAR(120) NOT NULL,
  job_id INT NULL,
  job_title VARCHAR(300) NULL,
  url VARCHAR(1024) NULL,
  result VARCHAR(1000) NULL,
  KEY ix_activity_ts (ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS platform_state (
  platform VARCHAR(40) PRIMARY KEY,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  last_run DATETIME NULL,
  last_error VARCHAR(1000) NULL,
  jobs_found INT NOT NULL DEFAULT 0,
  applications INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
