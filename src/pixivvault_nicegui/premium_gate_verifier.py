"""Salted activation verifier only. No password or reversible password material."""
ITERATIONS = 600000
SALT = bytes.fromhex('b705fae9fcecd16dc6645d6b9b3e912dfd1b3aeb07c5812357a5ec06ef1fa336')
VERIFIER = bytes.fromhex('6ab7319727eeba936e3f670ff6cdd004d23be61285aec98526ae1018e3d29930')
