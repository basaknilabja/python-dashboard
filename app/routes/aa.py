import pymysql

# Connect to MySQL
conn = pymysql.connect(
    host="localhost",
    user="root",
    passwd="root",   # your MySQL password
    database="teamtaurus"
)

cursor = conn.cursor()

# Open the image file in binary mode
with open(r"D:\Nilabja\Employee Photo\RAHULDE.jpeg", "rb") as file:
    binary_data = file.read()

# Update query (assuming empid='TT0016')
sql = "UPDATE public.emp_master SET photo = %s WHERE empid = %s"
values = (binary_data, "TT0008")

cursor.execute(sql, values)
conn.commit()

print("✅ Image uploaded successfully!")

cursor.close()
conn.close()
