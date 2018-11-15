import datetime
import os
import sys

import boto3
from awsglue.context import GlueContext
from awsglue.dynamicframe import DynamicFrame
from awsglue.job import Job
from awsglue.transforms import ApplyMapping
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import Row
from pytz import timezone


def create_diff_timestamp_list(device_id, list):
    result_list = []

    before_timestamp = 0

    for row in list:
        row_timestamp = row['timestamp']
        row_diff_timestamp = 0

        if before_timestamp != 0:
            row_diff_timestamp = row_timestamp - before_timestamp

        result_list.append(Row(
            device_id=device_id,
            timestamp=row_timestamp,
            diff_timestamp=row_diff_timestamp))

        before_timestamp = row_timestamp

    return result_list


def main(args, yesterday):
    sc = SparkContext()
    glueContext = GlueContext(sc)
    spark = glueContext.spark_session
    job = Job(glueContext)
    job.init(args['JOB_NAME'], args)

    database = args['database_name']
    table_name = '{0}kinesis_data'.format(args['table_prefix'])

    partition_predicate = \
        'partition_0="{0}" and partition_1="{1:02}" and partition_2="{2:02}"'.format(
            yesterday.year, yesterday.month, yesterday.day)

    datasource0 = glueContext.create_dynamic_frame.from_catalog(
        database=database,
        table_name=table_name,
        push_down_predicate=partition_predicate,
        transformation_ctx='datasource0')

    if datasource0.count() > 0:
        applymapping1 = ApplyMapping.apply(
            frame=datasource0,
            mappings=[
                ('device_id', 'string', 'device_id', 'string'),
                ('timestamp', 'long', 'timestamp', 'long')],
            transformation_ctx='applymapping1')

        df = applymapping1.toDF()
        dev = df.drop_duplicates(['device_id'])
        device_list = dev.collect()
        result_list = []

        for device in device_list:
            device_id = device['device_id']
            df2 = df[df.device_id == device_id]
            df_list = df2.sort('timestamp', ascending=True).collect()

            result_list.extend(create_diff_timestamp_list(device_id, df_list))

        df = spark.createDataFrame(result_list)
        dyf = DynamicFrame.fromDF(df, glueContext, 'dyf')

        # Hive format
        output_path = os.path.join(
            args['target_bucket'],
            'year={0}'.format(yesterday.year),
            'month={0:02}'.format(yesterday.month),
            'day={0:02}'.format(yesterday.day)
        )

        glueContext.write_dynamic_frame.from_options(
            frame=dyf,
            connection_type='s3',
            connection_options={'path': output_path},
            format='parquet',
            transformation_ctx='datasink2')

    job.commit()


if __name__ == '__main__':
    args = getResolvedOptions(sys.argv,
                              ['JOB_NAME',
                               'database_name',
                               'table_prefix',
                               'target_bucket'])
    now = datetime.datetime.now(timezone('UTC'))
    jst_now = now.astimezone(timezone('Asia/Tokyo'))

    yesterday = jst_now - datetime.timedelta(days=1)
    main(args, yesterday)
