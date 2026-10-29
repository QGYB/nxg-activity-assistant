# Third-party notices

This source archive contains project source and license notices, not vendored Python distributions, Microsoft Edge, browser binaries, or Windows system components. The locked dependencies are installed separately with `uv sync --locked`.

Project license: **AGPL-3.0-only**. Third-party texts below remain under their own licenses. Versions and license declarations were checked against the locked distributions’ installed metadata.

| Dependency | Locked version | Declared license | License text |
| --- | --- | --- | --- |
| colorama | 0.4.6 | None | [colorama-LICENSE.txt](LICENSES/colorama-LICENSE.txt) |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | [greenlet-LICENSE](LICENSES/greenlet-LICENSE), [greenlet-LICENSE.PSF](LICENSES/greenlet-LICENSE.PSF) |
| iniconfig | 2.3.1 | MIT | [iniconfig-LICENSE](LICENSES/iniconfig-LICENSE) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [packaging-LICENSE](LICENSES/packaging-LICENSE), [packaging-LICENSE.APACHE](LICENSES/packaging-LICENSE.APACHE), [packaging-LICENSE.BSD](LICENSES/packaging-LICENSE.BSD) |
| pillow | 13.0.0 | MIT-CMU | [pillow-LICENSE](LICENSES/pillow-LICENSE) |
| playwright | 1.64.1 | Apache-2.0 | [playwright-LICENSE](LICENSES/playwright-LICENSE) |
| pluggy | 1.7.0 | MIT | [pluggy-LICENSE](LICENSES/pluggy-LICENSE) |
| pyee | 14.0.0 | MIT | [pyee-LICENSE](LICENSES/pyee-LICENSE) |
| pygments | 2.21.0 | BSD-2-Clause | [pygments-AUTHORS](LICENSES/pygments-AUTHORS), [pygments-LICENSE](LICENSES/pygments-LICENSE) |
| pytest | 9.2.0 | MIT | [pytest-LICENSE](LICENSES/pytest-LICENSE) |
| ruff | 0.17.3 | MIT | [ruff-LICENSE](LICENSES/ruff-LICENSE) |
| typing-extensions | 4.16.0 | PSF-2.0 | [typing-extensions-LICENSE](LICENSES/typing-extensions-LICENSE) |

Runtime dependencies: Pillow, Playwright, greenlet, pyee and typing-extensions. The other distributions support development and tests. Python/Tkinter is provided by the separately installed Python runtime. Microsoft Edge is a separate installation governed by its vendor’s terms.

The project does not distribute the school website’s JavaScript, WeChat’s web client, a QQ client, browser profiles, account credentials or captured traffic. Interface URLs and field names describe interoperability. School, WeChat and Microsoft names identify the services used and do not indicate affiliation.
