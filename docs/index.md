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
- [**Migration Strategy**](migration.md): ETL implementation details from Oracle to MariaDB.
- [**NIRD Application Text**](nird_application.md): Text used in the Sigma2/NIRD application.
- [**Submitted Proposal (PDF)**](documents/NIRD-application.pdf): Final submitted NIRD application.
- [**Data Management Plan (DOCX)**](documents/data-management-plan.docx): Project data management plan.
