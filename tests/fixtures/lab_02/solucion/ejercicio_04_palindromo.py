# Ejercicio 4 — Detector de palíndromos
frase: str = input("Introduce una frase: ")

# Normalizar: todo en minúsculas y sin espacios
frase_limpia: str = frase.lower().replace(" ", "")

# Invertir con un trozo y comparar
frase_invertida: str = frase_limpia[::-1]
es_palindromo: bool = frase_limpia == frase_invertida

print()
print(f"Frase limpia:   {frase_limpia}")
print(f"Al revés:       {frase_invertida}")
print(f"Es palíndromo:  {es_palindromo}")
