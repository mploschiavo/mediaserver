"""Module entry point — ``python -m media_stack`` runs the controller.

Same callable as the ``media-stack-controller`` console-script
declared in ``pyproject.toml`` ``[project.scripts]``. Provides three
parallel ways to invoke the same code:

  * ``media-stack-controller --serve``      (post-pip-install)
  * ``python -m media_stack --serve``       (anywhere with the package on PYTHONPATH)
  * ``python -m media_stack.cli.commands.controller_main --serve``  (verbose form)

The first is the canonical container ENTRYPOINT. The second is what
operators reach for when they have the package installed but want
to invoke without remembering the script name. The third remains
for backward compatibility with any tooling that hard-codes the
module path.
"""

from media_stack.cli.commands.controller_main import main

if __name__ == "__main__":
    main()
