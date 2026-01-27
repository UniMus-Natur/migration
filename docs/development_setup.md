---
layout: default
title: Development Setup
nav_order: 2
---

# Development Setup

This guide explains how to set up your local environment for writing migration scripts.

## Prerequisite: The Submodule

This repository uses the official Specify 7 repository as a git submodule.
Ensure it is initialized:

```bash
git submodule update --init --recursive
```

## Python Environment

1.  **Install Requirements**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configuration**:
    The migration scripts need to connect to a database (either local or tunneled).
    
    *   Copy `config/local_specify_settings.py` (it is git-ignored).
    *   Edit it with your database credentials.

## The Bootstrap Mechanism

To use the Specify 7 Django ORM in standalone scripts, we use a `bootstrap.py` helper.

### How it works
1.  Adds the `specify7` submodule to `sys.path`.
2.  Injects `config.local_specify_settings` into `sys.modules` as `specifyweb.settings.local_specify_settings`.
3.  Calls `django.setup()`.

### Writing a Script
Start your script with:

```python
import bootstrap
bootstrap.setup()

from specifyweb.specify.models import Collection, Accession

# Now you can use the ORM!
print(Collection.objects.count())
```
