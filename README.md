This application uses S3 to store CSV files with NOAA climate data. Updloading files to the bucket triggers an SQS queue that 
operates the Lambda function to process the data in the CSV file
and generate a .txt file report, stored in S3, and metadata,
stored in DynamoDB