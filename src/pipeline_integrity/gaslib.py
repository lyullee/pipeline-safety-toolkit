"""Read GasLib geometry and nominations without inventing gas composition or active-device controls."""

import hashlib
import math
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

from .datasets import file_digest

GAS = "http://gaslib.zib.de/Gas"
FRAMEWORK = "http://gaslib.zib.de/Framework"


def _xml(archive, name, maximum_bytes):
    info = archive.getinfo(name)
    if info.file_size > maximum_bytes:
        raise ValueError("GasLib XML member exceeds the declared read budget")
    raw = archive.read(name)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("GasLib reader does not execute DTD or entity declarations")
    return ET.fromstring(raw), hashlib.sha256(raw).hexdigest(), b"creativecommons.org/licenses/by/3.0/" in raw


def _name(element):
    return element.tag.rsplit("}", 1)[-1]


def _quantities(element):
    result = {}
    for child in element:
        name = _name(child)
        if name in result:
            raise ValueError("duplicate GasLib property needs an explicit schema-specific reader")
        value = float(child.attrib["value"])
        if not math.isfinite(value):
            raise ValueError("GasLib quantities must be finite")
        result[name] = {"value": value, "unit": child.attrib.get("unit")}
    return result


def _length(q):
    scales = {"m": 1.0, "km": 1000.0, "mm": 0.001}
    if q["unit"] not in scales:
        raise ValueError("GasLib geometric units require a known explicit length unit")
    return q["value"] * scales[q["unit"]]


def read_gaslib(archive_path, *, network_member=None, scenario_member=None, maximum_xml_bytes=10000000):
    if type(maximum_xml_bytes) is not int or maximum_xml_bytes < 1:
        raise ValueError("provide a positive XML byte budget")
    path = Path(archive_path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise ValueError("duplicate ZIP member names are ambiguous")

        def choose(given, suffix):
            selected = [n for n in names if n.endswith(suffix)] if given is None else [given]
            if len(selected) != 1:
                raise ValueError("select one unambiguous GasLib member")
            return selected[0]

        network_member = choose(network_member, ".net")
        scenario_member = choose(scenario_member, ".scn")
        network, net_hash, net_license = _xml(archive, network_member, maximum_xml_bytes)
        scenario, scn_hash, scn_license = _xml(archive, scenario_member, maximum_xml_bytes)
    if network.tag != f"{{{GAS}}}network" or scenario.tag != f"{{{GAS}}}boundaryValue":
        raise ValueError("unexpected GasLib root schema")
    node_container = network.find(f"{{{FRAMEWORK}}}nodes")
    link_container = network.find(f"{{{FRAMEWORK}}}connections")
    if node_container is None or link_container is None:
        raise ValueError("GasLib topology containers are absent")
    nodes, links, pipes = {}, {}, {}
    for node in node_container:
        name = node.attrib["id"]
        if not name.strip() or name in nodes:
            raise ValueError("GasLib node identifiers must be unique")
        q = _quantities(node)
        nodes[name] = dict(
            kind=_name(node),
            quantities=q,
            elevation_m=_length(q["height"]),
            plotting_coordinates={k: node.attrib[k] for k in ("x", "y") if k in node.attrib},
        )
    for link in link_container:
        name = link.attrib["id"]
        if not name.strip() or name in links:
            raise ValueError("GasLib connection identifiers must be unique")
        start, end = link.attrib["from"], link.attrib["to"]
        if start not in nodes or end not in nodes or start == end:
            raise ValueError("GasLib connection endpoints are invalid")
        kind, q = _name(link), _quantities(link)
        links[name] = dict(kind=kind, start_node=start, end_node=end, quantities=q)
        if kind == "pipe":
            length, diameter, roughness = (_length(q[key]) for key in ("length", "diameter", "roughness"))
            if length <= 0 or diameter <= 0 or roughness < 0:
                raise ValueError("GasLib pipe dimensions are not physically admissible")
            pipes[name] = dict(
                start_node=start,
                end_node=end,
                length_m=length,
                diameter_m=diameter,
                roughness_m=roughness,
                elevation_change_m=nodes[end]["elevation_m"] - nodes[start]["elevation_m"],
                thermal_source_parameters={k: v for k, v in q.items() if "heat" in k.lower()},
            )
    nominations = {}
    for selected in scenario:
        sid = selected.attrib["id"]
        if not sid.strip() or sid in nominations:
            raise ValueError("GasLib scenarios need unique identifiers")
        rows = {}
        for node in selected:
            name = node.attrib["id"]
            if name not in nodes or name in rows:
                raise ValueError("GasLib nomination references an absent or duplicate node")
            bounds = []
            for item in node:
                value = float(item.attrib["value"])
                if not math.isfinite(value):
                    raise ValueError("GasLib nomination bounds must be finite")
                bounds.append(
                    dict(
                        quantity=_name(item),
                        bound=item.attrib["bound"],
                        value=value,
                        unit=item.attrib["unit"],
                    )
                )
            rows[name] = dict(type=node.attrib["type"], bounds=bounds)
        nominations[sid] = rows
    missing = [
        "reviewed gas composition and EOS mapping",
        "pressure reference and selected boundary states",
        "nomination volume/power basis and mass conversion",
        "thermal boundary and active equipment configuration",
    ]
    active = {name: row["kind"] for name, row in links.items() if row["kind"] != "pipe"}
    return dict(
        schema="pipeline_integrity.gaslib.v1",
        archive_sha256=file_digest(path),
        network_member=network_member,
        network_member_sha256=net_hash,
        scenario_member=scenario_member,
        scenario_member_sha256=scn_hash,
        inline_cc_by_3_0_markers_present=net_license and scn_license,
        nodes=nodes,
        connections=links,
        pipes=pipes,
        nominations=nominations,
        node_types=dict(Counter(n["kind"] for n in nodes.values())),
        connection_types=dict(Counter(n["kind"] for n in links.values())),
        active_devices_needing_mapping=active,
        missing_mapping=missing,
        complete_network_run_status="needs_data",
        pressure_reference_inferred=False,
        gas_composition_inferred=False,
        nomination_is_measured_time_history=False,
    )
