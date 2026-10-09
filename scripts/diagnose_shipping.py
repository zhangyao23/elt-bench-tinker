"""Manual SQL on official source snapshots/GT in DuckDB: version diagnostic, not official rollout."""

import copy
import shutil
from pathlib import Path

from elt_tinker.environment import Episode
from elt_tinker.task import Task, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]

TRUCKS_SQL = """CREATE OR REPLACE TABLE trucks AS
WITH ranked AS (
 SELECT s.*, c.cust_name,
 ROW_NUMBER() OVER (PARTITION BY s.truck_id ORDER BY s.weight DESC, c.cust_name ASC) AS rn
 FROM raw.shipment s JOIN raw.customer c ON s.cust_id=c.cust_id
), stats AS (
 SELECT truck_id, COUNT(*) AS num_shipments,
 100.0*AVG(CASE WHEN weight>10000 THEN 1.0 ELSE 0.0 END) AS per_weight_exceed_10000,
 SUM(CASE WHEN EXTRACT(YEAR FROM ship_date)=2017 THEN 1 ELSE 0 END)
 -SUM(CASE WHEN EXTRACT(YEAR FROM ship_date)=2016 THEN 1 ELSE 0 END) AS num_shipments_change_from_2016_to_2017
 FROM raw.shipment GROUP BY truck_id
)
SELECT t.truck_id,t.make,t.model_year,
 CASE WHEN t.make='Peterbilt' THEN 'Texas (TX)' WHEN t.make='Mack' THEN 'North Carolina (NC)'
 WHEN t.make='Kenworth' THEN 'Washington (WA)' END AS headquarter,
 COALESCE(s.num_shipments,0) AS num_shipments,r.cust_name AS person_receive_heaviest_shipment,
 s.per_weight_exceed_10000,COALESCE(s.num_shipments_change_from_2016_to_2017,0) AS num_shipments_change_from_2016_to_2017
FROM raw.truck t LEFT JOIN stats s ON t.truck_id=s.truck_id
LEFT JOIN ranked r ON t.truck_id=r.truck_id AND r.rn=1"""

DRIVERS_SQL = """CREATE OR REPLACE TABLE drivers AS
WITH firsts AS (
 SELECT driver_id,weight,ROW_NUMBER() OVER (PARTITION BY driver_id ORDER BY ship_date ASC,weight DESC) AS rn
 FROM raw.shipment
), stats AS (
 SELECT s.driver_id,
 SUM(CASE WHEN EXTRACT(YEAR FROM s.ship_date)=2017 THEN 1 ELSE 0 END) AS num_shipments_2017,
 SUM(CASE WHEN c.population=(SELECT MIN(population) FROM raw.city) THEN 1 ELSE 0 END) AS num_shipment_to_least_populated_city,
 100.0*AVG(CASE WHEN cu.cust_name='Autoware Inc' THEN 1.0 ELSE 0.0 END) AS per_shipment_placed_by_Autoware_Inc,
 MAX(CASE WHEN s.weight>0.95*(SELECT AVG(weight) FROM raw.shipment) THEN 1 ELSE 0 END) AS has_a_shipment_weight_greater_95_per_avg_across_all_shipments
 FROM raw.shipment s LEFT JOIN raw.city c ON s.city_id=c.city_id
 LEFT JOIN raw.customer cu ON s.cust_id=cu.cust_id GROUP BY s.driver_id
)
SELECT d.driver_id,d.first_name,d.last_name,COALESCE(s.num_shipments_2017,0) AS num_shipments_2017,
 COALESCE(s.num_shipment_to_least_populated_city,0) AS num_shipment_to_least_populated_city,
 s.per_shipment_placed_by_Autoware_Inc,
 s.has_a_shipment_weight_greater_95_per_avg_across_all_shipments,f.weight AS weight_first_shipment
FROM raw.driver d LEFT JOIN stats s ON d.driver_id=s.driver_id
LEFT JOIN firsts f ON d.driver_id=f.driver_id AND f.rn=1"""


def main():
    official = ROOT / "data/official/shipping"
    output = ROOT / "runs/shipping_snapshot_diagnostic"
    public = output / "public"
    public.mkdir(parents=True, exist_ok=True)
    spec = copy.deepcopy(read_json(official / "public/task.json"))
    spec.update(official=False, execution_mode="manual_snapshot_diagnostic")
    for table, source in spec["sources"].items():
        file = (
            official / "source_snapshot/driver.parquet"
            if table == "driver"
            else official / "source_snapshot/truck.csv"
            if table == "truck"
            else official / f"source_snapshot/data/shipping/{table}.csv"
        )
        shutil.copyfile(file, public / file.name)
        source.update(kind="file", path=file.name, format="parquet" if table == "driver" else "csv")
    write_json(public / "task.json", spec)
    task = Task.load(public, official / "hidden")
    episode = Episode(task, "duckdb", output / "execution")
    try:
        for table in spec["sources"]:
            assert episode.act("configure_load", {"table": table, "source_kind": "file"})["ok"]
        assert episode.act("run_load", {})["ok"]
        for sql in (TRUCKS_SQL, DRIVERS_SQL):
            result = episode.act("transform", {"sql": sql})
            if not result["ok"]:
                raise RuntimeError(result["error"])
        episode.act("submit_pipeline", {})
        grade = episode.grade
        print(
            {
                "scope": "manual snapshot diagnostic; no credentialed model rollout",
                "valid": grade["valid"],
                "reward": grade["reward"],
                "models": grade["models"],
            }
        )
        if not grade.get("all_models_match"):
            raise SystemExit(1)
    finally:
        episode.close()


if __name__ == "__main__":
    main()
