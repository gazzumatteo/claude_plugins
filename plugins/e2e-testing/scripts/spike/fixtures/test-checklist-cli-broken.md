# E2E — CLI parser-edge-case test

Step con tag linguaggio non riconosciuto, per verificare la diagnostica `step_definition.txt`.

## Step con tag yaml ma menziona docker

Esegui il `docker compose ps` configurato nel file seguente. Il parser deve riconoscere needs_cli=True per via della parola "docker" ma non estrarre comandi dal blocco yaml.

```yaml
services:
  api:
    image: example/api
```

## Step bash regolare (controllo)

Verifica che il parser estragga ancora questo.

```bash
echo "ok"
```
