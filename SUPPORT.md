# Getting help

recall is maintained by one person in their spare time, so please use the
channel that fits your question:

| You want to… | Go to |
|---|---|
| Ask how to install, configure or use recall | [Discussions → Q&A](https://github.com/andreshma/recall/discussions/categories/q-a) |
| Share an idea or discuss a design change before coding it | [Discussions → Ideas](https://github.com/andreshma/recall/discussions/categories/ideas) |
| Report a reproducible bug | [New issue → Bug report](https://github.com/andreshma/recall/issues/new?template=bug_report.yml) |
| Propose a concrete feature | [New issue → Feature request](https://github.com/andreshma/recall/issues/new?template=feature_request.yml) |
| Report a security vulnerability | [Private advisory](https://github.com/andreshma/recall/security/advisories/new) — never a public issue (see [SECURITY.md](SECURITY.md)) |

Before asking, please check the [User Manual](docs/USAGE.md) (installation,
configuration, troubleshooting) and [TESTING.md](docs/TESTING.md).

When you report a problem, include the output of `python -I -m recall status`,
your Windows and Python versions, and whether the service runs on DirectML or
CPU. Do not paste your prompts, `service.json`, its token, or text from your
books.

There is no guaranteed response time. Pull requests that follow
[CONTRIBUTING.md](CONTRIBUTING.md) are the fastest way to get a fix in.
