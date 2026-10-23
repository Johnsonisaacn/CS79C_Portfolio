"""
Lambda function that processes CSV files containing NOAA climate data,
summarizes the data, and saves a report to S3 and metadata to DynamoDB
"""

import os
import io
import csv
import json
import boto3
from datetime import datetime, timezone

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

DEST_BUCKET = os.environ["DEST_BUCKET"]
TABLE_NAME = os.environ["TABLE_NAME"]
COL_DATE = "DATE"
COL_NAME = "NAME"
COL_VAL = "DailyDepartureFromNormalAverageTemperature"


def parse_iso_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def summarize(csv_text):
    """Return aggregated stats from CSV text."""
    station_name = None
    year = None
    pos = neg = 0
    max_val = None
    min_val = None

    reader = csv.DictReader(io.StringIO(csv_text))
    missing = {COL_DATE, COL_NAME, COL_VAL} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    for row in reader:
        if station_name is None and row.get(COL_NAME, "").strip():
            station_name = row[COL_NAME].strip()

        raw_val = (row.get(COL_VAL) or "").strip()
        if not raw_val:
            continue

        dt = parse_iso_date(row.get(COL_DATE, ""))
        if dt is None:
            continue
        if year is None:
            year = dt.year

        try:
            temp = float(raw_val)
        except ValueError:
            continue

        if temp > 0:
            pos += 1
        elif temp < 0:
            neg += 1

        if max_val is None or temp > max_val:
            max_val = temp
        if min_val is None or temp < min_val:
            min_val = temp

    if max_val is None:
        raise ValueError("No valid DATE/temperature pairs found in CSV")

    return {
        "year": year,
        "station_name": station_name or "Unknown Station",
        "positive_count": pos,
        "negative_count": neg,
        "greatest_departure": max_val,
        "lowest_departure": min_val,
    }


def render_report(stats):
    return (
        f"Climate Departure Report\n"
        f"========================\n"
        f"Year:                     {stats['year']}\n"
        f"Station:                  {stats['station_name']}\n"
        f"\n"
        f"Positive departures:      {stats['positive_count']}\n"
        f"Negative departures:      {stats['negative_count']}\n"
        f"\n"
        f"Greatest departure:       {stats['greatest_departure']:.2f} \u00b0C\n"
        f"Lowest departure:         {stats['lowest_departure']:.2f} \u00b0C\n"
    )


def lambda_handler(event, context):
    # Unwrap SQS message if present
    record = event["Records"][0]
    if "body" in record:
        record = json.loads(record["body"])["Records"][0]
    src_bucket = record["s3"]["bucket"]["name"]
    src_key = record["s3"]["object"]["key"].replace("+", " ").replace("%20", " ")

    print(f"Processing s3://{src_bucket}/{src_key}")

    obj = s3.get_object(Bucket=src_bucket, Key=src_key)
    csv_text = obj["Body"].read().decode("utf-8-sig")

    stats = summarize(csv_text)
    report_text = render_report(stats)

    base = os.path.splitext(os.path.basename(src_key))[0]
    dest_key = f"reports/{base}_{stats['year']}.txt"

    s3.put_object(
        Bucket=DEST_BUCKET,
        Key=dest_key,
        Body=report_text.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )
    print(f"Saved s3://{DEST_BUCKET}/{dest_key}")

    dynamodb.Table(TABLE_NAME).put_item(Item={
        "pk": src_key,
        "station_name": stats["station_name"],
        "year": stats["year"],
        "positive_count": stats["positive_count"],
        "negative_count": stats["negative_count"],
        "greatest_departure": str(stats["greatest_departure"]),
        "lowest_departure": str(stats["lowest_departure"]),
        "output_bucket": DEST_BUCKET,
        "output_key": dest_key,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "statusCode": 200,
        "body": json.dumps({
            "station": stats["station_name"],
            "year": stats["year"],
            "output": f"s3://{DEST_BUCKET}/{dest_key}",
        }),
    }