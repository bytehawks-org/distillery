"""
Jinja2 template engine for configuration value resolution.

Supports multi-pass rendering for nested templates like:
{{ registry_url }}/{{ namespace }}/{{ build.image_basename }}:{{ this.name }}
"""

from jinja2 import Environment, BaseLoader, TemplateError, StrictUndefined
from typing import Dict, Any, Optional
import re


class DistilleryTemplateEngine:
    """
    Template engine with support for:
    - Multi-pass rendering (resolve nested templates)
    - Special variables ({{ this.* }} for self-reference)
    - Strict mode (fail on undefined variables)
    """

    def __init__(self, max_passes: int = 5, strict: bool = True):
        """
        Initialize template engine.

        Args:
            max_passes: Maximum number of rendering passes
            strict: If True, raise error on undefined variables
        """
        self.max_passes = max_passes

        if strict:
            self.env = Environment(loader=BaseLoader(), undefined=StrictUndefined)
        else:
            self.env = Environment(loader=BaseLoader())

        # Track rendering depth for debugging
        self._render_depth = 0

    def render(self,
               template_str: str,
               context: Dict[str, Any],
               current_item: Optional[Dict[str, Any]] = None) -> str:
        """
        Render a template string with multi-pass resolution.

        Args:
            template_str: Template string (may contain {{ variables }})
            context: Global context dictionary
            current_item: Current item for {{ this.* }} references

        Returns:
            Fully resolved string

        Raises:
            TemplateError: If template fails to converge or has errors
        """
        if not isinstance(template_str, str):
            return template_str

        # Build rendering context
        render_context = dict(context)

        # Add 'this' for self-reference
        if current_item:
            render_context['this'] = current_item

        previous = None
        current = template_str

        for pass_num in range(self.max_passes):
            # Check convergence
            if current == previous:
                return current

            previous = current

            try:
                template = self.env.from_string(current)
                current = template.render(render_context)

            except Exception as e:
                raise TemplateError(
                    f"Template rendering failed at pass {pass_num + 1}: {e}\n"
                    f"Template: {template_str[:200]}...\n"
                    f"Context keys: {list(render_context.keys())}"
                ) from e

        # Did not converge
        raise TemplateError(
            f"Template did not converge after {self.max_passes} passes.\n"
            f"Template: {template_str[:200]}...\n"
            f"Last result: {current[:200]}..."
        )

    def render_dict(self,
                    data: Dict[str, Any],
                    context: Dict[str, Any],
                    current_item: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Recursively render all string values in a dictionary.

        Args:
            data: Dictionary with potential template strings
            context: Global context
            current_item: Current item for {{ this.* }}

        Returns:
            Dictionary with all templates resolved
        """
        result = {}

        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.render(value, context, current_item)
            elif isinstance(value, dict):
                result[key] = self.render_dict(value, context, current_item)
            elif isinstance(value, list):
                result[key] = self.render_list(value, context, current_item)
            else:
                result[key] = value

        return result

    def render_list(self,
                    data: list,
                    context: Dict[str, Any],
                    current_item: Optional[Dict[str, Any]] = None) -> list:
        """Recursively render list items"""
        result = []

        for item in data:
            if isinstance(item, str):
                result.append(self.render(item, context, current_item))
            elif isinstance(item, dict):
                result.append(self.render_dict(item, context, current_item))
            elif isinstance(item, list):
                result.append(self.render_list(item, context, current_item))
            else:
                result.append(item)

        return result

    @staticmethod
    def has_template_vars(text: str) -> bool:
        """Check if string contains template variables"""
        return bool(re.search(r'\{\{.*?\}\}', text))