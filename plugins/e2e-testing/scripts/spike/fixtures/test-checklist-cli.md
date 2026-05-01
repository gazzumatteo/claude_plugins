# E2E — CLI smoke (local runner test)

Pure-CLI checklist used to validate the `run_cli_only_step` path of the local runner. Tutti i comandi sono stabili, locali, senza dipendenze di rete.

## Stampa hello

Esegui il comando seguente e verifica che l'output contenga la parola "hello".

```bash
python3 -c "print('hello world from e2e runner')"
```

## Aritmetica

Calcola 2+2 e verifica che l'output sia esattamente `4`.

```bash
echo $((2 + 2))
```

## Recovery da fallimento

Esegui un comando che fallisce ma ne incatena un altro che salva la situazione. L'output finale deve contenere `recovered`.

```bash
false || echo recovered
```
