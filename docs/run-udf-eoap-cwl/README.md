# run_udf EOAP-CWL Integration

This folder documents the full design, research, and implementation of CWL execution
via the standard OpenEO `run_udf` process with `runtime=EOAP-CWL`.

## Files

| File | Contents |
|------|----------|
| [architecture.md](architecture.md) | System architecture, flow diagrams, component overview |
| [vito-research.md](vito-research.md) | Findings from VITO/EOEPCA codebase research |
| [implementation.md](implementation.md) | What was built, key files, PRs |
| [staged-data-flow.md](staged-data-flow.md) | How save_result → context → run_udf works |
| [testing.md](testing.md) | How to test, example process graphs, known issues |
| [roadmap.md](roadmap.md) | Remaining work (Phases 4–5) |

## Quick Reference

- **GitHub Epic**: https://github.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows/issues/58
- **Staged data flow issue**: https://github.com/Eurac-Research-Institute-for-EO/openeo-argoworkflows/issues/93
- **Runtime name**: `EOAP-CWL`
- **Parameter mapping**: `udf` → CWL doc, `context` → CWL inputs, `data` → ignored (pass None)
- **Live endpoint**: `https://openeo.eurac.edu/openeo/1.1.0/udf_runtimes`
