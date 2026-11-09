# Exercise 3 — Secure WiFi password
network_name: str = input("Nombre de la red (SSID): ")
password: str = input("Clave WiFi: ")

# Each requirement is an independent bool
length_ok: bool = len(password) >= 12
has_upper: bool = password != password.lower()
has_lower: bool = password != password.upper()
no_spaces: bool = " " not in password
has_symbols: bool = not password.isalnum()
no_network_name: bool = network_name.lower() not in password.lower()

# Score: True is worth 1 and False is worth 0 when converted to int
score: int = (int(length_ok) + int(has_upper) + int(has_lower)
              + int(no_spaces) + int(has_symbols) + int(no_network_name))
secure: bool = score == 6

print()
print(f"--- Análisis de la clave de «{network_name}» ---")
print(f"Al menos 12 caracteres:        {length_ok}")
print(f"Tiene mayúsculas:              {has_upper}")
print(f"Tiene minúsculas:              {has_lower}")
print(f"Sin espacios:                  {no_spaces}")
print(f"Tiene símbolos:                {has_symbols}")
print(f"No contiene el nombre de red:  {no_network_name}")
print(f"Puntuación:                    {score}/6")
print(f"Clave segura:                  {secure}")
