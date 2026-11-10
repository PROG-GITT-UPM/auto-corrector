# Exercise 4 — Palindrome detector
sentence: str = input("Introduce una frase: ")

# Normalize: all lower case and without spaces
clean_sentence: str = sentence.lower().replace(" ", "")

# Reverse with a slice and compare
reversed_sentence: str = clean_sentence[::-1]
is_palindrome: bool = clean_sentence == reversed_sentence

print()
print(f"Frase limpia:   {clean_sentence}")
print(f"Al revés:       {reversed_sentence}")
print(f"Es palíndromo:  {is_palindrome}")
