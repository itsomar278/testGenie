"""C# semantic parsing using tree-sitter."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tree_sitter_c_sharp as ts_csharp
from tree_sitter import Language, Parser, Node

from dotnet_test_generator.core.exceptions import ParsingError
from dotnet_test_generator.utils.logging import get_logger
from dotnet_test_generator.utils.json_utils import JsonHandler

logger = get_logger(__name__)


@dataclass
class MethodInfo:
    """Information about a method."""

    name: str
    return_type: str
    parameters: list[dict]
    modifiers: list[str]
    line_start: int
    line_end: int
    is_async: bool = False
    is_static: bool = False
    attributes: list[str] = field(default_factory=list)
    body_preview: str = ""


@dataclass
class PropertyInfo:
    """Information about a property."""

    name: str
    type: str
    modifiers: list[str]
    line_start: int
    has_getter: bool = True
    has_setter: bool = True
    attributes: list[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    """Information about a class or interface."""

    name: str
    kind: str  # class, interface, struct, record
    namespace: str
    base_types: list[str]
    modifiers: list[str]
    line_start: int
    line_end: int
    methods: list[MethodInfo] = field(default_factory=list)
    properties: list[PropertyInfo] = field(default_factory=list)
    nested_types: list["ClassInfo"] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)


@dataclass
class FileParseResult:
    """Result of parsing a C# file."""

    file_path: str
    namespaces: list[str]
    usings: list[str]
    classes: list[ClassInfo]
    errors: list[str] = field(default_factory=list)


class CSharpParser:
    """
    C# semantic parser using tree-sitter.

    Parses C# source files and extracts structured information about
    namespaces, classes, methods, and properties.
    """

    def __init__(self):
        """Initialize the parser."""
        self.language = Language(ts_csharp.language())
        self.parser = Parser(self.language)

    def parse_file(self, file_path: Path) -> FileParseResult:
        """
        Parse a C# file and extract semantic information.

        Args:
            file_path: Path to the C# file

        Returns:
            FileParseResult with parsed information
        """
        logger.debug(f"Parsing file: {file_path}")

        try:
            content = file_path.read_text(encoding="utf-8-sig")
        except Exception as e:
            raise ParsingError(
                f"Failed to read file: {e}",
                file_path=str(file_path),
            ) from e

        return self.parse_content(content, str(file_path))

    def parse_content(self, content: str, file_path: str = "<string>") -> FileParseResult:
        """
        Parse C# content and extract semantic information.

        Args:
            content: C# source code
            file_path: File path for error reporting

        Returns:
            FileParseResult with parsed information
        """
        tree = self.parser.parse(content.encode("utf-8"))
        root = tree.root_node

        errors = []
        if root.has_error:
            errors.append("Parse tree contains errors")

        usings = self._extract_usings(root, content)
        namespaces = self._extract_namespaces(root, content)
        classes = self._extract_classes(root, content, "")

        return FileParseResult(
            file_path=file_path,
            namespaces=namespaces,
            usings=usings,
            classes=classes,
            errors=errors,
        )

    def _get_node_text(self, node: Node, content: str) -> str:
        """Get text content of a node."""
        return content[node.start_byte:node.end_byte]

    def _extract_usings(self, root: Node, content: str) -> list[str]:
        """Extract using directives."""
        usings = []
        for child in root.children:
            if child.type == "using_directive":
                name_node = child.child_by_field_name("name")
                if name_node:
                    usings.append(self._get_node_text(name_node, content))
        return usings

    def _extract_namespaces(self, root: Node, content: str) -> list[str]:
        """Extract namespace declarations."""
        namespaces = []

        def find_namespaces(node: Node):
            if node.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    namespaces.append(self._get_node_text(name_node, content))
            for child in node.children:
                find_namespaces(child)

        find_namespaces(root)
        return namespaces

    def _extract_modifiers(self, node: Node, content: str) -> list[str]:
        """Extract modifiers from a declaration."""
        modifiers = []
        for child in node.children:
            if child.type == "modifier":
                modifiers.append(self._get_node_text(child, content))
        return modifiers

    def _extract_attributes(self, node: Node, content: str) -> list[str]:
        """Extract attributes from a declaration."""
        attributes = []
        for child in node.children:
            if child.type == "attribute_list":
                for attr in child.children:
                    if attr.type == "attribute":
                        name_node = attr.child_by_field_name("name")
                        if name_node:
                            attributes.append(self._get_node_text(name_node, content))
        return attributes

    def _extract_classes(
        self,
        node: Node,
        content: str,
        current_namespace: str,
    ) -> list[ClassInfo]:
        """Extract class/interface/struct/record declarations."""
        classes = []

        type_declarations = [
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "record_declaration",
        ]

        for child in node.children:
            # Update namespace context
            namespace = current_namespace
            if child.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
                name_node = child.child_by_field_name("name")
                if name_node:
                    namespace = self._get_node_text(name_node, content)
                # Recurse into namespace body
                body = child.child_by_field_name("body")
                if body:
                    classes.extend(self._extract_classes(body, content, namespace))
                else:
                    # File-scoped namespace - continue with siblings
                    continue

            if child.type in type_declarations:
                class_info = self._parse_type_declaration(child, content, namespace)
                if class_info:
                    classes.append(class_info)

            # Recurse into declaration lists
            if child.type == "declaration_list":
                classes.extend(self._extract_classes(child, content, namespace))

        return classes

    def _parse_type_declaration(
        self,
        node: Node,
        content: str,
        namespace: str,
    ) -> ClassInfo | None:
        """Parse a class/interface/struct/record declaration."""
        kind_map = {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "struct_declaration": "struct",
            "record_declaration": "record",
        }

        name_node = node.child_by_field_name("name")
        if not name_node:
            return None

        name = self._get_node_text(name_node, content)
        kind = kind_map.get(node.type, "class")
        modifiers = self._extract_modifiers(node, content)
        attributes = self._extract_attributes(node, content)

        # Extract base types
        base_types = []
        bases_node = node.child_by_field_name("bases")
        if bases_node:
            for base_child in bases_node.children:
                if base_child.type in ("identifier", "generic_name", "qualified_name"):
                    base_types.append(self._get_node_text(base_child, content))

        # Extract members
        methods = []
        properties = []
        nested_types = []

        body = node.child_by_field_name("body")
        if body:
            for member in body.children:
                if member.type == "method_declaration":
                    method = self._parse_method(member, content)
                    if method:
                        methods.append(method)
                elif member.type == "property_declaration":
                    prop = self._parse_property(member, content)
                    if prop:
                        properties.append(prop)
                elif member.type in (
                    "class_declaration",
                    "interface_declaration",
                    "struct_declaration",
                ):
                    nested = self._parse_type_declaration(member, content, namespace)
                    if nested:
                        nested_types.append(nested)

        return ClassInfo(
            name=name,
            kind=kind,
            namespace=namespace,
            base_types=base_types,
            modifiers=modifiers,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            methods=methods,
            properties=properties,
            nested_types=nested_types,
            attributes=attributes,
        )

    def _parse_method(self, node: Node, content: str) -> MethodInfo | None:
        """Parse a method declaration."""
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None

        name = self._get_node_text(name_node, content)
        modifiers = self._extract_modifiers(node, content)
        attributes = self._extract_attributes(node, content)

        # Return type
        return_type = "void"
        type_node = node.child_by_field_name("type")
        if type_node:
            return_type = self._get_node_text(type_node, content)

        # Parameters
        parameters = []
        params_node = node.child_by_field_name("parameters")
        if params_node:
            for param in params_node.children:
                if param.type == "parameter":
                    param_type = param.child_by_field_name("type")
                    param_name = param.child_by_field_name("name")
                    if param_type and param_name:
                        parameters.append({
                            "name": self._get_node_text(param_name, content),
                            "type": self._get_node_text(param_type, content),
                        })

        # Body preview (first few lines)
        body_preview = ""
        body_node = node.child_by_field_name("body")
        if body_node:
            body_text = self._get_node_text(body_node, content)
            lines = body_text.split("\n")[:5]
            body_preview = "\n".join(lines)
            if len(body_text.split("\n")) > 5:
                body_preview += "\n..."

        return MethodInfo(
            name=name,
            return_type=return_type,
            parameters=parameters,
            modifiers=modifiers,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            is_async="async" in modifiers,
            is_static="static" in modifiers,
            attributes=attributes,
            body_preview=body_preview,
        )

    def _parse_property(self, node: Node, content: str) -> PropertyInfo | None:
        """Parse a property declaration."""
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None

        name = self._get_node_text(name_node, content)
        modifiers = self._extract_modifiers(node, content)
        attributes = self._extract_attributes(node, content)

        # Type
        prop_type = "object"
        type_node = node.child_by_field_name("type")
        if type_node:
            prop_type = self._get_node_text(type_node, content)

        # Accessors
        has_getter = False
        has_setter = False
        accessors = node.child_by_field_name("accessors")
        if accessors:
            accessor_text = self._get_node_text(accessors, content).lower()
            has_getter = "get" in accessor_text
            has_setter = "set" in accessor_text or "init" in accessor_text
        else:
            # Auto-property with => syntax
            has_getter = True

        return PropertyInfo(
            name=name,
            type=prop_type,
            modifiers=modifiers,
            line_start=node.start_point[0] + 1,
            has_getter=has_getter,
            has_setter=has_setter,
            attributes=attributes,
        )

    def parse_directory(self, directory: Path, output_file: Path | None = None) -> dict:
        """
        Parse all C# files in a directory.

        Args:
            directory: Directory to parse
            output_file: Optional file to save JSON output

        Returns:
            Dictionary with all parsed files
        """
        logger.info(f"Parsing directory: {directory}")

        results = {}
        cs_files = list(directory.rglob("*.cs"))
        logger.info(f"Found {len(cs_files)} C# files")

        for cs_file in cs_files:
            try:
                result = self.parse_file(cs_file)
                relative_path = str(cs_file.relative_to(directory))
                results[relative_path] = self._result_to_dict(result)
            except ParsingError as e:
                logger.warning(f"Failed to parse {cs_file}: {e}")
                results[str(cs_file.relative_to(directory))] = {"error": str(e)}

        if output_file:
            JsonHandler.dump_file(results, output_file)
            logger.info(f"Saved parse results to {output_file}")

        return results

    def _result_to_dict(self, result: FileParseResult) -> dict:
        """Convert FileParseResult to dictionary."""
        return {
            "file_path": result.file_path,
            "namespaces": result.namespaces,
            "usings": result.usings,
            "classes": [self._class_to_dict(c) for c in result.classes],
            "errors": result.errors,
        }

    def _class_to_dict(self, cls: ClassInfo) -> dict:
        """Convert ClassInfo to dictionary."""
        return {
            "name": cls.name,
            "kind": cls.kind,
            "namespace": cls.namespace,
            "base_types": cls.base_types,
            "modifiers": cls.modifiers,
            "line_start": cls.line_start,
            "line_end": cls.line_end,
            "attributes": cls.attributes,
            "methods": [
                {
                    "name": m.name,
                    "return_type": m.return_type,
                    "parameters": m.parameters,
                    "modifiers": m.modifiers,
                    "line_start": m.line_start,
                    "line_end": m.line_end,
                    "is_async": m.is_async,
                    "is_static": m.is_static,
                    "attributes": m.attributes,
                }
                for m in cls.methods
            ],
            "properties": [
                {
                    "name": p.name,
                    "type": p.type,
                    "modifiers": p.modifiers,
                    "line_start": p.line_start,
                    "has_getter": p.has_getter,
                    "has_setter": p.has_setter,
                    "attributes": p.attributes,
                }
                for p in cls.properties
            ],
            "nested_types": [self._class_to_dict(n) for n in cls.nested_types],
        }

    def get_searchable_index(self, parse_results: dict) -> dict:
        """
        Create a searchable index from parse results.

        Args:
            parse_results: Dictionary of parsed files

        Returns:
            Searchable index with classes, methods, and properties
        """
        index = {
            "classes": {},
            "methods": {},
            "properties": {},
            "namespaces": {},
        }

        for file_path, result in parse_results.items():
            if "error" in result:
                continue

            for ns in result.get("namespaces", []):
                if ns not in index["namespaces"]:
                    index["namespaces"][ns] = []
                index["namespaces"][ns].append(file_path)

            for cls in result.get("classes", []):
                fqn = f"{cls['namespace']}.{cls['name']}" if cls["namespace"] else cls["name"]
                index["classes"][fqn] = {
                    "file": file_path,
                    "kind": cls["kind"],
                    "line": cls["line_start"],
                    "base_types": cls["base_types"],
                }

                for method in cls.get("methods", []):
                    method_fqn = f"{fqn}.{method['name']}"
                    index["methods"][method_fqn] = {
                        "file": file_path,
                        "class": fqn,
                        "line": method["line_start"],
                        "return_type": method["return_type"],
                        "parameters": method["parameters"],
                    }

                for prop in cls.get("properties", []):
                    prop_fqn = f"{fqn}.{prop['name']}"
                    index["properties"][prop_fqn] = {
                        "file": file_path,
                        "class": fqn,
                        "line": prop["line_start"],
                        "type": prop["type"],
                    }

        return index
