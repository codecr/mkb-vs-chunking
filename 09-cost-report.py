#!/usr/bin/env python3
"""
Costo real del benchmark via Cost Explorer. El numero que produce este
script es el que va al articulo (ver README, seccion "Costo estimado").

GOTCHAS CONFIRMADOS (ver skill aws-billing-and-cost-management):
  - Las cost allocation tags tardan 24h en aparecer tras activarse, y SOLO
    cubren costo incurrido DESPUES de la activacion (no retroactivo). El
    tag Project=mkb-vs-chunking lo pone terraform (default_tags), pero si
    no se activo a mano en Billing > Cost allocation tags ANTES de correr
    01-terraform-apply.sh, el filtro por tag devuelve vacio en silencio
    (Cost Explorer no distingue "filtro valido sin datos" de "filtro
    invalido"). Por eso este script SIEMPRE corre tambien el desglose por
    SERVICE sin filtro de tag, como contraste.
  - Los nombres de servicio de Cost Explorer no siempre son obvios (p.ej.
    EC2 se parte en dos). No se adivina el nombre exacto para S3 Vectors:
    se descubre en runtime con GetDimensionValues.
  - Nunca se suman montos "a ojo": todo el calculo lo hace este script.

Uso:
    source config.sh && python3 09-cost-report.py --start 2026-09-01 --end 2026-09-08
"""
import argparse
import json
import os
import pathlib

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
RESULTS = pathlib.Path(os.environ.get("RESULTS_DIR", "results"))
PROJECT = os.environ.get("PROJECT", "mkb-vs-chunking")

# Cost Explorer es global mente un endpoint unico (siempre us-east-1).
ce = boto3.client("ce", region_name="us-east-1")


def discover_services(keywords: list) -> list:
    """No se adivinan nombres de servicio de Cost Explorer: se descubren."""
    resp = ce.get_dimension_values(
        TimePeriod={"Start": "2026-01-01", "End": "2026-01-02"},
        Dimension="SERVICE",
    )
    names = [v["Value"] for v in resp["DimensionValues"]]
    matched = [n for n in names if any(k.lower() in n.lower() for k in keywords)]
    return matched


def check_tag_activated(tag_key: str) -> bool:
    """ListCostAllocationTags (no GetTags): es la API que realmente expone
    el Status Active/Inactive de una cost allocation tag."""
    try:
        resp = ce.list_cost_allocation_tags(TagKeys=[tag_key])
        tags = resp.get("CostAllocationTags", [])
        if not tags:
            print(f"[WARN] tag '{tag_key}' no existe como cost allocation tag "
                  f"(ni Active ni Inactive) -- nunca se activo.")
            return False
        return tags[0]["Status"] == "Active"
    except Exception as exc:
        print(f"[WARN] no se pudo verificar el tag '{tag_key}': {exc}")
        return False


def cost_by_service(start: str, end: str, tag_filter: dict | None = None) -> dict:
    kwargs: dict = dict(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        Filter={
            "Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}
        },
    )
    if tag_filter:
        kwargs["Filter"] = {
            "And": [kwargs["Filter"], {"Tags": tag_filter}]
        }

    # GetCostAndUsage no tiene paginator registrado en botocore
    # (can_paginate() = False); se pagina a mano con NextPageToken.
    totals: dict = {}
    next_token = None
    while True:
        if next_token:
            kwargs["NextPageToken"] = next_token
        resp = ce.get_cost_and_usage(**kwargs)
        for day in resp["ResultsByTime"]:
            for group in day["Groups"]:
                service = group["Keys"][0]
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                totals[service] = totals.get(service, 0.0) + amount
        next_token = resp.get("NextPageToken")
        if not next_token:
            break
    return totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (inicio del benchmark)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD, exclusivo")
    args = ap.parse_args()

    print(f"[cost] rango: {args.start} .. {args.end} (fin exclusivo)")
    print("[cost] NOTA: Cost Explorer tiene ~24h de retraso; el ultimo dia "
          "del rango puede estar incompleto/estimado.")

    tag_ok = check_tag_activated("Project")
    print(f"[cost] tag 'Project' activado como cost allocation tag: {tag_ok}")
    if not tag_ok:
        print("[cost] [ATENCION] sin activar, el desglose por tag de abajo "
              "puede salir vacio SIN error -- no confundir con costo cero real. "
              "Activar en Billing > Cost allocation tags y esperar 24h no es "
              "retroactivo a costo ya incurrido.")

    print("\n[cost] === Por servicio, SIN filtro de tag (universo completo de la cuenta) ===")
    by_service_all = cost_by_service(args.start, args.end)
    for svc, amt in sorted(by_service_all.items(), key=lambda kv: -kv[1]):
        if amt > 0.001:
            print(f"  {svc:<45s} ${amt:.4f}")
    total_all = sum(by_service_all.values())
    print(f"  {'TOTAL (toda la cuenta, no solo el benchmark)':<45s} ${total_all:.4f}")

    by_service_tagged = {}
    if tag_ok:
        print(f"\n[cost] === Por servicio, CON tag Project={PROJECT} ===")
        by_service_tagged = cost_by_service(
            args.start, args.end, tag_filter={"Key": "Project", "Values": [PROJECT]}
        )
        for svc, amt in sorted(by_service_tagged.items(), key=lambda kv: -kv[1]):
            if amt > 0.001:
                print(f"  {svc:<45s} ${amt:.4f}")
        total_tagged = sum(by_service_tagged.values())
        print(f"  {'TOTAL (Project={})'.format(PROJECT):<45s} ${total_tagged:.4f}")

    print("\n[cost] === Servicios relevantes detectados (no adivinados) ===")
    relevant = discover_services(["bedrock", "s3", "vector"])
    print(f"  {relevant}")

    report = {
        "start": args.start,
        "end": args.end,
        "tag_activated": tag_ok,
        "by_service_all_account": by_service_all,
        "by_service_tagged": by_service_tagged,
        "total_all_account": total_all,
        "total_tagged": sum(by_service_tagged.values()) if by_service_tagged else None,
        "relevant_service_names_discovered": relevant,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "cost-report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[cost] -> {out}")


if __name__ == "__main__":
    main()
