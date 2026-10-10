"""
Component Registry - Dependency injection and component management.

Provides centralized component registration and dependency resolution.
Components can be registered and retrieved by interface or name.
"""

import logging
from typing import Dict, Any, Type, Optional, List, TypeVar

logger = logging.getLogger(__name__)

# TypeVar for generic type hints in component retrieval methods
T = TypeVar("T")


class ComponentRegistry:
    """
    Registry for managing component dependencies and instances.

    Provides dependency injection and component lifecycle management.
    Components are registered by interface and can be retrieved by type or name.
    """

    def __init__(self) -> None:
        self._components: Dict[Type[Any], Any] = {}
        self._named_components: Dict[str, Any] = {}
        self._interfaces: Dict[Type[Any], List[Any]] = {}

        logger.info("ComponentRegistry initialized")

    def register(
        self,
        component: Any,
        name: Optional[str] = None,
        interfaces: Optional[List[Type[Any]]] = None,
    ) -> None:
        """
        Register a component in the registry.

        Args:
            component: Component instance to register
            name: Optional name for named lookup
            interfaces: List of interfaces this component implements
        """
        component_type = type(component)

        # Register by concrete type
        self._components[component_type] = component

        # Register by name if provided
        if name:
            self._named_components[name] = component
            logger.debug(
                f"Registered component '{name}' of type {component_type.__name__}"
            )

        # Register by interfaces
        if interfaces:
            for interface in interfaces:
                if interface not in self._interfaces:
                    self._interfaces[interface] = []
                self._interfaces[interface].append(component)
                logger.debug(
                    f"Registered {component_type.__name__} as {interface.__name__}"
                )

        logger.info(f"Component registered: {component_type.__name__}")

    def get(self, component_type: Type[T]) -> Optional[T]:
        """
        Get component by type.

        Args:
            component_type: Type of component to retrieve

        Returns:
            Component instance or None if not found
        """
        return self._components.get(component_type)

    def get_by_name(self, name: str) -> Optional[Any]:
        """
        Get component by name.

        Args:
            name: Registered name of component

        Returns:
            Component instance or None if not found
        """
        return self._named_components.get(name)

    def get_by_interface(self, interface: Type[T]) -> List[T]:
        """
        Get all components that implement an interface.

        Args:
            interface: Interface type to search for

        Returns:
            List of components implementing the interface
        """
        return self._interfaces.get(interface, [])

    def get_single_by_interface(self, interface: Type[T]) -> Optional[T]:
        """
        Get single component by interface (expects exactly one).

        Args:
            interface: Interface type to search for

        Returns:
            Single component instance or None if not found or multiple exist
        """
        components = self.get_by_interface(interface)
        if len(components) == 1:
            return components[0]
        elif len(components) > 1:
            logger.warning(
                f"Multiple components implement {interface.__name__}, use get_by_interface()"
            )
            return None
        else:
            return None

    def unregister(self, component_type: Type[Any]) -> bool:
        """
        Unregister a component by type.

        Args:
            component_type: Type of component to remove

        Returns:
            True if component was removed, False if not found
        """
        if component_type in self._components:
            component = self._components[component_type]
            del self._components[component_type]

            # Remove from named components
            named_to_remove = []
            for name, comp in self._named_components.items():
                if comp is component:
                    named_to_remove.append(name)
            for name in named_to_remove:
                del self._named_components[name]

            # Remove from interfaces
            for interface, components in self._interfaces.items():
                if component in components:
                    components.remove(component)
                    if not components:
                        del self._interfaces[interface]

            logger.info(f"Component unregistered: {component_type.__name__}")
            return True

        return False

    def list_components(self) -> Dict[str, Any]:
        """
        List all registered components.

        Returns:
            Dictionary with component information
        """
        result: Dict[str, Any] = {
            "by_type": {
                t.__name__: c.__class__.__name__ for t, c in self._components.items()
            },
            "by_name": {
                name: c.__class__.__name__ for name, c in self._named_components.items()
            },
            "by_interface": {
                i.__name__: [c.__class__.__name__ for c in comps]
                for i, comps in self._interfaces.items()
            },
        }

        result["total_components"] = len(self._components)
        result["total_named"] = len(self._named_components)
        result["total_interfaces"] = len(self._interfaces)

        return result

    def clear(self) -> None:
        """Clear all registered components."""
        self._components.clear()
        self._named_components.clear()
        self._interfaces.clear()
        logger.info("Component registry cleared")

    def is_registered(self, component_type: Type[Any]) -> bool:
        """
        Check if a component type is registered.

        Args:
            component_type: Type to check

        Returns:
            True if registered, False otherwise
        """
        return component_type in self._components

    def validate_dependencies(self) -> Dict[str, Any]:
        """
        Validate that all registered components are healthy.

        Returns:
            Dictionary with validation results
        """
        results: Dict[str, Any] = {"healthy": [], "unhealthy": [], "errors": []}

        for component_type, component in self._components.items():
            try:
                if hasattr(component, "is_healthy") and callable(component.is_healthy):
                    if component.is_healthy():
                        results["healthy"].append(component_type.__name__)
                    else:
                        results["unhealthy"].append(component_type.__name__)
                else:
                    # Assume healthy if no health check method
                    results["healthy"].append(component_type.__name__)
            except Exception as e:
                results["errors"].append(
                    {"component": component_type.__name__, "error": str(e)}
                )

        results["total_checked"] = (
            len(results["healthy"]) + len(results["unhealthy"]) + len(results["errors"])
        )
        results["all_healthy"] = (
            len(results["unhealthy"]) == 0 and len(results["errors"]) == 0
        )

        return results


# Global registry instance
_component_registry = ComponentRegistry()


def get_component_registry() -> ComponentRegistry:
    """Get the global component registry instance."""
    return _component_registry
