---
layout: home
title: Home
nav_order: 1
---

# Welcome to the MUSIT to Specify Migration Knowledge Base

This documentation covers the planning, architecture, and execution of migrating the Norwegian University Museums (MUSIT) collection management system to Specify.

## Sections

- [**Development Setup**](development_setup.md): Set up the local Python environment.
- [**Kubernetes Deployment**](kubernetes_deployment.md): Deploy the staging stack with Helm.
- [**Architecture**](architecture.md): Data flow and system component overview.
- [**Infrastructure**](infrastructure.md): Source (MUSIT), staging (Sigma2), and production (AWS) details.
- [**Database Strategy**](database_strategy.md): Single-db vs multi-db strategy analysis.
- [**Risk Assessment**](risk_assessment.md): Risk analysis for the selected database strategy.
- [**Database Fields**](database_fields.md): Auto-generated schema reference and ERD.
- [**Oracle Schema Overview**](oracle_schema_overview.md): Deep analysis and grouping of the legacy source schemas and tables.
- [**Oracle botany datasets**](oracle_botany_datasets.md): MUSIT `DATASET` / `PROJECT_NAME`, legacy USD botany schemas, extractable dimensions, and SQL.
- [**Migration Strategy (Phased)**](migration_strategy.md): Strategy for merging shared data and iteratively migrating datasets to Specify 7.
- [**User migration report**](user_migration_report.md): `migration_report.json` from the Migrate Users Prefect flow (Phase 1.4).
- [**MUSIT collection agents migration**](migrate_musit_agents.md): `ACTOR` / `PERSON_NAME` → Specify `Agent` (Phase 1.1; Prefect flow `migrate_musit_agents_flow`).
- [**Specify structure sync**](sync_specify_structure.md): `sync_specify_structure_flow` — create divisions, disciplines, and collections from YAML (post-bootstrap, idempotent).
- [**Specify forms git sync**](specify_forms_git_sync.md): Form XML and export/import scripts in the [`specify7-forms`](../specify7-forms/) repository.
- [**Migration reports on S3**](migration_s3_reports.md): Shared bucket folder layout and `report.json` conventions.
- [**Migration (ETL Technical)**](migration.md): ETL pipeline implementation details from Oracle to MariaDB.
- [**NIRD Application Text**](nird_application.md): Text used in the Sigma2/NIRD application.
- [**Specify + Feide SSO**](specify_feide_sso.md): Feide OIDC integration notes, onboarding model, and rollout guidance.
- [**Submitted Proposal (PDF)**](documents/NIRD-application.pdf): Final submitted NIRD application.
- [**Data Management Plan (DOCX)**](documents/data-management-plan.docx): Project data management plan.
