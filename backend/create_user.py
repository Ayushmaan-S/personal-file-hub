from auth import hash_password

password = input("Enter your password: ")

hashed = hash_password(password)

print("\nYour password hash:")
print(hashed)