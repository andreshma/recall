# Governance

## Roles

- **Maintainer** — [@andreshma](https://github.com/andreshma). Reviews and
  merges pull requests, triages issues, cuts releases and handles security
  reports. The maintainer has the final say on scope and design.
- **Contributors** — anyone who opens an issue, a discussion or a pull request.
  Contributors who keep making good changes may be invited as maintainers.

## How decisions are made

- Day-to-day changes are decided in the pull request that makes them.
- Changes to an architectural decision (anything in [docs/adr/](docs/adr/))
  need a new or superseding ADR in the same pull request, with the measurement
  that justifies it. The project's rule is that thresholds, floors and model
  choices follow measured numbers, not preference (see
  [CONTRIBUTING.md](CONTRIBUTING.md)).
- Larger proposals start as a [Discussion](https://github.com/andreshma/recall/discussions/categories/ideas)
  so the design can be agreed before anyone writes code.

## Merging

All changes to `main` go through a pull request with green CI (unit tests,
lint and types, supply chain, CodeQL). Force pushes and deletion of `main` are
blocked.

## Releases

Versions follow [Semantic Versioning](https://semver.org/). Each release has a
section in [CHANGELOG.md](CHANGELOG.md), a `vX.Y.Z` tag and a GitHub release;
`CITATION.cff`, `pyproject.toml` and `recall.__version__` carry the same
version (a test checks it).

## Security

Vulnerabilities are handled privately as described in [SECURITY.md](SECURITY.md).

## Code of conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
