---
paths:
  - "supabase/**"
---
One migration file per change. Enable RLS on every new table and add a pgTAP test
in supabase/tests proving who can and cannot read it. Write the tests first.
Transactional writes are Postgres functions. Never touch staging or production.
