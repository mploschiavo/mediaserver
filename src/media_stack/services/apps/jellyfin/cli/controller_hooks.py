"""Bootstrap hook entrypoints for Jellyfin app-specific actions."""

from __future__ import annotations

from collections.abc import Callable

from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient

from .jellyfin_plugin_activation_service import (
    JellyfinPluginActivationConfig,
    JellyfinPluginActivationService,
)


class JellyfinControllerHooks:

    def activate_media_server_plugins(self, 
        *,
        namespace: str,
        kube: KubernetesClient,
        info: Callable[[str], None],
        warn: Callable[[str], None],
        deployment_exists: Callable[[str], bool],
        restart_deployment: Callable[[str, int], None],
        read_secret_key: Callable[[str, str], str],
    ) -> None:
        """Activate pending Jellyfin plugins by restarting deployment when needed."""
        JellyfinPluginActivationService(
            cfg=JellyfinPluginActivationConfig(namespace=namespace),
            kube=kube,
            info=info,
            warn=warn,
            deployment_exists=deployment_exists,
            restart_deployment=restart_deployment,
            read_secret_key=read_secret_key,
        ).activate_plugins_if_needed()


_instance = JellyfinControllerHooks()
activate_media_server_plugins = _instance.activate_media_server_plugins
