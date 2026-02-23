import hashlib
import json
import random

# --- 1. Finite Field Mathematics Helper Functions ---

def modular_inverse(a, m):
    """Compute the modular inverse of a modulo m."""
    return pow(a, -1, m)

# --- 2. Elliptic Curve Definition (using the Bitcoin curve parameters for example) ---
# Parameters for the secp256k1 curve:
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F # Prime modulus
A = 0
B = 7
G_X = 0x79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 # Generator point x
G_Y = 0x483ada7726a3c4655da4fbfc0e1108a8fd17b448a68554199c47d08ffb10d4b8 # Generator point y
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141 # Order of the group

class FiniteFieldElement:
    
    def __init__(self, value, prime=N):
        self.value = value % prime
        self.prime = prime

    def __add__(self, other):
        return FiniteFieldElement(self.value + other.value, self.prime)

    def __radd__(self, other):
        if isinstance(other, FiniteFieldElement):
            return other.__add__(self)
        else:
            return FiniteFieldElement(other + self.value, self.prime)

    def __sub__(self, other):
        return FiniteFieldElement(self.value - other.value, self.prime)

    def __mul__(self, other):
        if isinstance(other, FiniteFieldElement):
            return FiniteFieldElement(self.value * other.value, self.prime)
        else:
            # other is a scalar (int or float)
            return FiniteFieldElement(self.value * other, self.prime)

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        inv = pow(other.value, self.prime - 2, self.prime)
        return FiniteFieldElement(self.value * inv, self.prime)

    def __repr__(self):
        return f"FiniteFieldElement({self.value} mod {self.prime})"

    def __pow__(self, exponent):
        return FiniteFieldElement(pow(self.value, exponent.value, self.prime), self.prime)
    
    def __xor__(self, exponent):
        return self.__pow__(exponent)
    
    def __eq__(self, other):
        if isinstance(other, FiniteFieldElement):
            return self.value == other.value and self.prime == other.prime
        else:
            # Allow comparison with integers
            return self.value == other
    
    def __ne__(self, value):
        return not self.__eq__(value)

class EncryptedFiniteFieldElement:
    """
    Represents an encrypted FiniteFieldElement with homomorphic properties.
    Stores the encrypted value, ephemeral public key, and shared secret for operations.
    """
    
    def __init__(self, encrypted_value, ephemeral_public_key, shared_secret=None):
        """
        Initialize an encrypted field element.
        
        Args:
            encrypted_value: FiniteFieldElement representing the encrypted data
            ephemeral_public_key: Point used for encryption
            shared_secret: FiniteFieldElement of the shared secret (optional, for homomorphic ops)
        """
        self.encrypted_value = encrypted_value
        self.ephemeral_public_key = ephemeral_public_key
        self.shared_secret = shared_secret
    
    def __add__(self, other):
        """
        Homomorphic addition: E(a) + E(b) = E(a + b)
        Properly adjusts for the extra shared secret term.
        """
        if not isinstance(other, EncryptedFiniteFieldElement):
            raise TypeError("Can only add EncryptedFiniteFieldElement to EncryptedFiniteFieldElement")
        
        # Check that both use the same ephemeral key (same shared secret)
        if self.ephemeral_public_key != other.ephemeral_public_key:
            raise ValueError("Cannot add encrypted values with different ephemeral keys")
        
        # E(a) + E(b) = (a + s) + (b + s) = a + b + 2s
        # We want E(a + b) = a + b + s, so subtract one shared secret
        sum_encrypted = self.encrypted_value + other.encrypted_value
        
        if self.shared_secret is not None:
            adjusted_sum = sum_encrypted - self.shared_secret
        else:
            # Without shared_secret, we can't properly adjust
            adjusted_sum = sum_encrypted
        
        return EncryptedFiniteFieldElement(adjusted_sum, self.ephemeral_public_key, self.shared_secret)
    
    def __mul__(self, scalar):
        """
        Homomorphic scalar multiplication: k * E(m) = E(k * m)
        When multiplying two encrypted elements, performs homomorphic addition: E(a) * E(b) = E(a+b)
        If the encrypted values have different ephemeral keys, the right operand is adjusted to match.
        Properly adjusts for the extra shared secret terms.
        
        Args:
            scalar: FiniteFieldElement, int, or EncryptedFiniteFieldElement
                   - If EncryptedFiniteFieldElement: performs E(a) * E(b) = E(a+b) (homomorphic addition)
                   - If FiniteFieldElement or int: performs scalar multiplication
        """
        if isinstance(scalar, EncryptedFiniteFieldElement):
            # Multiply two encrypted values interpreted as homomorphic addition: E(a) * E(b) = E(a+b)
            # First, check if they have the same ephemeral key
            if self.ephemeral_public_key == scalar.ephemeral_public_key:
                # Same ephemeral key, use __add__ directly
                return self.__add__(scalar)
            else:
                # Different ephemeral keys: adjust the right operand to match the left operand's key
                # We need to add the encrypted values but account for the right operand's shared secret
                # If right operand has shared_secret s_2 and left has s_1:
                # E_2(b) = b + s_2, and we want to extract just b to add to E_1(a) = a + s_1
                # So we compute: (a + s_1) + (b + s_2) - s_2 = a + b + s_1
                sum_encrypted = self.encrypted_value + scalar.encrypted_value
                
                # Subtract right operand's shared secret if available
                # This gives us: (a + s_1) + (b + s_2) - s_2 = a + b + s_1
                if scalar.shared_secret is not None:
                    sum_encrypted = sum_encrypted - scalar.shared_secret
                
                # Return with left operand's ephemeral key and shared secret
                # The encrypted value is now (a + b + s_1), which is correct for E_1(a + b)
                return EncryptedFiniteFieldElement(sum_encrypted, self.ephemeral_public_key, self.shared_secret)
        
        if isinstance(scalar, int):
            scalar = FiniteFieldElement(scalar, N)
        elif not isinstance(scalar, FiniteFieldElement):
            raise TypeError("Scalar must be FiniteFieldElement, int, or EncryptedFiniteFieldElement, not type {}, value {}".format(type(scalar), scalar))
        
        # k * E(m) = k * (m + s) = k*m + k*s
        # We want E(k*m) = k*m + s, so subtract (k-1)*s
        scaled = self.encrypted_value * scalar
        
        if self.shared_secret is not None:
            adjustment = (scalar - FiniteFieldElement(1, N)) * self.shared_secret
            adjusted_scaled = scaled - adjustment
        else:
            # Without shared_secret, we can't properly adjust
            adjusted_scaled = scaled
        
        return EncryptedFiniteFieldElement(adjusted_scaled, self.ephemeral_public_key, self.shared_secret)
    
    def __pow__(self, exponent):
        """
        Homomorphic exponentiation: E(m)^k = E(k * m)
        Properly adjusts for the extra shared secret terms.
        
        Args:
            exponent: FiniteFieldElement, int, or EncryptedFiniteFieldElement representing the exponent
        """
        # If exponent is encrypted, we need to decrypt it first (requires private key)
        # For now, we'll decrypt it by treating it as a scalar operation
        if isinstance(exponent, EncryptedFiniteFieldElement):
            # Use the encrypted value directly as the scalar multiplier
            # This treats E(k)^E(m) as E(m)^k where k is the encrypted value
            exponent_to_use = exponent.encrypted_value
        elif isinstance(exponent, int):
            exponent_to_use = FiniteFieldElement(exponent, N)
        elif isinstance(exponent, FiniteFieldElement):
            exponent_to_use = exponent
        else:
            raise TypeError("Exponent must be FiniteFieldElement, int, or EncryptedFiniteFieldElement not type {}, value {}".format(type(exponent), exponent))
        
        # E(m)^k = (m + s)^k = k*m + (k-1)*s (approximation for small k)
        # We want E(k*m) = k*m + s, so subtract ((k-1)*s) from the result
        powered = self.encrypted_value * exponent_to_use
        
        if self.shared_secret is not None:
            adjustment = (exponent_to_use - FiniteFieldElement(1, N)) * self.shared_secret
            adjusted_powered = powered - adjustment
        else:
            # Without shared_secret, we can't properly adjust
            adjusted_powered = powered
        
        return EncryptedFiniteFieldElement(adjusted_powered, self.ephemeral_public_key, self.shared_secret)
    
    def __rmul__(self, scalar):
        """Support scalar * encrypted_element syntax."""
        return self.__mul__(scalar)
    
    def decrypt(self, private_key):
        """
        Decrypt this encrypted element using the private key.
        
        Args:
            private_key: The private key corresponding to the public key used for encryption
            
        Returns:
            FiniteFieldElement containing the decrypted plaintext
        """
        return decrypt_message(private_key, self.ephemeral_public_key, self.encrypted_value)
    
    def __repr__(self):
        return f"EncryptedFiniteFieldElement(value={self.encrypted_value.value})"

    def deserialize(self, data):
        """Deserialize from a dictionary."""
        data = json.loads(data)
        self.encrypted_value = FiniteFieldElement(data['encrypted_value'], N)
        self.ephemeral_public_key = Point(None, None).deserialize(data['ephemeral_public_key'])
        if 'shared_secret' in data:
            self.shared_secret = FiniteFieldElement(data['shared_secret'], N)
        else:
            self.shared_secret = None
        return self
    def serialize(self):
        
        """Serialize to a dictionary."""
        data = {
            'encrypted_value': self.encrypted_value.value,
            'ephemeral_public_key': self.ephemeral_public_key.serialize()
        }
        if self.shared_secret is not None:
            data['shared_secret'] = self.shared_secret.value
        return json.dumps(data)
    
class Point:
    def __init__(self, x, y):
        if x is None and y is None:
            self.x = None
            self.y = None
            return # Point at infinity
        
        # Wrap coordinates in FiniteFieldElement with prime P
        self.x = FiniteFieldElement(x, P) if not isinstance(x, FiniteFieldElement) else x
        self.y = FiniteFieldElement(y, P) if not isinstance(y, FiniteFieldElement) else y
        
        # Verify point is on the curve: y^2 = x^3 + Ax + B
        left = FiniteFieldElement(self.y.value ** 2, P)
        right = FiniteFieldElement(self.x.value ** 3 + A * self.x.value + B, P)
        if left.value != right.value:
            raise ValueError("Point is not on the curve")

    def __eq__(self, other):
        if self.x is None and other.x is None:
            return True
        if self.x is None or other.x is None:
            return False
        return self.x.value == other.x.value and self.y.value == other.y.value

    def __add__(self, other):
        if other.x is None: return self
        if self.x is None: return other
        if self.x.value == other.x.value and self.y.value != other.y.value:
            return Point(None, None) # Point at infinity

        if self.x.value == other.x.value: # Point doubling
            s_num = (3 * self.x.value * self.x.value + A) % P
            s_den = (2 * self.y.value) % P
            s = (s_num * modular_inverse(s_den, P)) % P
        else: # Point addition
            s_num = (other.y.value - self.y.value) % P
            s_den = (other.x.value - self.x.value) % P
            s = (s_num * modular_inverse(s_den, P)) % P

        x3 = (s * s - self.x.value - other.x.value) % P
        y3 = (s * (self.x.value - x3) - self.y.value) % P
        return Point(x3, y3)

    def scalar_multiplication(self, k):
        """Efficient 'double and add' method for k*P."""
        if k % N == 0:
            return Point(None, None)
        if k < 0:
            # Need to implement point negation for negative k
            pass

        result = Point(None, None) # Start with point at infinity
        add = self
        
        # Binary expansion of k
        while k > 0:
            if k % 2 == 1:
                result = result + add
            add = add + add
            k //= 2
        return result
    
    def serialize(self) -> str:
        """Serialize the point to bytes (uncompressed format)."""
        if self.x is None:
            byte_data = b'\x00' # Point at infinity
        byte_data = b'\x04' + self.x.value.to_bytes(32, 'big') + self.y.value.to_bytes(32, 'big')
        
        hex_string = byte_data.hex()
        
        return hex_string
    
    def deserialize(self, hex_string):
        """Deserialize bytes to a Point (uncompressed format)."""
        
        bytes_object = bytes.fromhex(hex_string)
        
        if bytes_object[0] == 0x00:
            return Point(None, None) # Point at infinity
        if bytes_object[0] != 0x04:
            raise ValueError("Only uncompressed format supported")
        x = int.from_bytes(bytes_object[1:33], 'big')
        y = int.from_bytes(bytes_object[33:65], 'big')
        return Point(x, y)

# --- 3. Key Generation and Dummy Signing/Verification (ECDSA) ---

def generate_key_pair():
    private_key = random.randint(1, N - 1)
    public_key = Point(G_X, G_Y).scalar_multiplication(private_key)
    return private_key, public_key

def load_key_pair(file_path):
    """Load a key pair from a PEM file."""
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    private_key_line = lines[1].strip()
    public_key_line = lines[4].strip()
    
    private_key = int(private_key_line, 16)
    public_key = Point(None, None).deserialize(public_key_line)
    
    return private_key, public_key

def save_key_pair(private_key, public_key, file_path):
    """Save a key pair to a PEM file."""
    with open(file_path, 'w') as f:
        f.write("-----BEGIN SHAMIRA PRIVATE KEY-----\n")
        f.write(f"{private_key:064x}\n")
        f.write("-----END SHAMIRA PRIVATE KEY-----\n")
        f.write("-----BEGIN SHAMIRA PUBLIC KEY-----\n")
        f.write(f"{public_key.serialize()}\n")
        f.write("-----END SHAMIRA PUBLIC KEY-----\n") 


def sign_message(private_key, message):
    """
    Simplified signature generation (r, s).
    Note: 'k' must be a new, secret random number for every signature.
    """
    z = int.from_bytes(hashlib.sha256(message).digest(), 'big')
    k = random.randint(1, N - 1) # Must be truly random and secret
    P_k = Point(G_X, G_Y).scalar_multiplication(k)
    r = P_k.x.value % N
    if r == 0: return sign_message(private_key, message) # Recalculate if r is 0
    
    k_inv = modular_inverse(k, N)
    s = (k_inv * (z + r * private_key)) % N
    if s == 0: return sign_message(private_key, message) # Recalculate if s is 0

    return r, s

def verify_signature(public_key, message, signature):
    """Simplified signature verification."""
    r, s = signature
    #print(f"Verifying signature: r={r}, s={s}")
    if not (1 <= r <= N - 1 and 1 <= s <= N - 1):
        return False
    
    z = int.from_bytes(hashlib.sha256(message).digest(), 'big')
    s_inv = modular_inverse(s, N)
    u1 = (z * s_inv) % N
    u2 = (r * s_inv) % N
    
    # R = u1*G + u2*Q, where Q is the public key
    point_g = Point(G_X, G_Y)
    R = point_g.scalar_multiplication(u1) + public_key.scalar_multiplication(u2)

    if R.x is None: return False
    return R.x.value % N == r

def encrypt_message(public_key, plaintext_element, ephemeral_public_key=None, shared_secret_n=None, return_class=True):
    """
    Encrypt a FiniteFieldElement using elliptic curve cryptography (ECIES-like).
    Uses FiniteFieldElement for all operations.
    
    Args:
        public_key: Point representing the recipient's public key
        plaintext_element: FiniteFieldElement or int to encrypt
        ephemeral_public_key: Optional pre-existing ephemeral public key (must provide shared_secret_n too)
        shared_secret_n: Optional pre-computed shared secret (must provide ephemeral_public_key too)
        return_class: If True, returns EncryptedFiniteFieldElement; if False, returns tuple
    
    Returns:
        If return_class=True: EncryptedFiniteFieldElement
        If return_class=False: (ephemeral_public_key, encrypted_element)
    """
    # Ensure plaintext is a FiniteFieldElement
    if not isinstance(plaintext_element, FiniteFieldElement):
        plaintext_element = FiniteFieldElement(plaintext_element, N)
    
    # If ephemeral key and shared secret are provided, use them
    if ephemeral_public_key is not None and shared_secret_n is not None:
        encrypted_element = plaintext_element + shared_secret_n
    else:
        # Generate new ephemeral key pair
        ephemeral_private = FiniteFieldElement(random.randint(1, N - 1), N)
        ephemeral_public_key = Point(G_X, G_Y).scalar_multiplication(ephemeral_private.value)
        
        # Compute shared secret: S = ephemeral_private * public_key
        shared_point = public_key.scalar_multiplication(ephemeral_private.value)
        
        # Use the x-coordinate as shared secret (it's already a FiniteFieldElement)
        shared_secret = shared_point.x
        
        # Convert shared_secret to work with prime N
        shared_secret_n = FiniteFieldElement(shared_secret.value, N)
        
        encrypted_element = plaintext_element + shared_secret_n
    
    if return_class:
        return EncryptedFiniteFieldElement(encrypted_element, ephemeral_public_key, shared_secret_n)
    else:
        return ephemeral_public_key, encrypted_element

def decrypt_message(private_key, ephemeral_public_key, encrypted_element):
    """
    Decrypt a FiniteFieldElement encrypted with encrypt_message.
    Uses FiniteFieldElement for all operations.
    Returns the plaintext FiniteFieldElement.
    """
    # Convert private key to FiniteFieldElement
    private_field = FiniteFieldElement(private_key, N)
    
    # Compute shared secret: S = private_key * ephemeral_public_key
    shared_point = ephemeral_public_key.scalar_multiplication(private_field.value)
    
    # Use the x-coordinate as shared secret (it's a FiniteFieldElement)
    shared_secret = shared_point.x
    
    # Convert shared_secret to work with prime N
    shared_secret_n = FiniteFieldElement(shared_secret.value, N)
    
    # Decrypt by subtracting the shared secret from the encrypted element
    plaintext_element = encrypted_element - shared_secret_n
    
    return plaintext_element

class PolynomialPoint:
    def __init__(self, x: FiniteFieldElement, y: FiniteFieldElement):
        self.x = x
        self.y = y
        
    def serialize(self) -> str:
        return f"({self.x.value},{self.y.value})"
    
    def deserialize(self, data: str):
        x_str, y_str = data.strip("()").split(",")
        self.x = FiniteFieldElement(int(x_str), P)
        self.y = FiniteFieldElement(int(y_str), P)
        
    def __add__(self, other):
        if self.x != other.x:
            raise ValueError("Can only add PolynomialPoints with the same x value. self.x = {}, other.x = {}".format(self.x, other.x))
        
        return PolynomialPoint(self.x, self.y + other.y)

    def __mul__(self, scalar):
        if not isinstance(scalar, int):
            raise TypeError("Scalar multiplication only supports integers.")
        
        return PolynomialPoint(self.x, self.y * FiniteFieldElement(scalar, N))
    
def polynomial_evaluation(coef: list[FiniteFieldElement], x: FiniteFieldElement, c=FiniteFieldElement(0, N)):
    result = c
    for i in range(len(coef)):
        result += coef[i] * (x ** FiniteFieldElement(i+1, N))
    return result

def lagrange_interpolation(points: list[PolynomialPoint], x: FiniteFieldElement):
    total = FiniteFieldElement(0, N)
    n = len(points)
    for i in range(n):
        xi, yi = points[i].x, points[i].y
        term = yi
        for j in range(n):
            if j != i:
                xj = points[j].x
                term *= (x - xj) / (xi - xj)
        total += term
    return (total)

def get_shares(S, n_shares, k_threshold, x_coords=None):
    
    n_coefs = (k_threshold - 1)
    coef = []

    for i in range(n_coefs):
        coef.append(FiniteFieldElement(random.randint(0, N-1), N))
        print(f"Coefficient {i}: {coef[i]}")

    points=[]
    for i in range(n_shares):
        x = FiniteFieldElement(x_coords[i], N) if x_coords else FiniteFieldElement(i + 1, N)
        y = polynomial_evaluation(coef, x, FiniteFieldElement(S, N))
        points.append(PolynomialPoint(x, y))
        print(f"Point {i}: ({x}, {y})")

    return points

# # --- Example Usage ---

# # 1. Generate keys
# priv_key, pub_key = generate_key_pair()
# print(f"Private Key (int): {priv_key}")
# print(f"Public Key (Point): X={pub_key.x.value}, Y={pub_key.y.value}\n")

# # 2. Sign a message
# msg = b"This is the field data to be secured."
# r_val, s_val = sign_message(priv_key, msg)
# print(f"Signature (r, s): r={r_val}, s={s_val}\n")

# # 3. Verify the signature
# is_valid = verify_signature(pub_key, msg, (r_val, s_val))
# print(f"Signature valid? {is_valid}")

# # 4. Verify with a manipulated message
# manipulated_msg = b"This is different data."
# is_valid_manipulated = verify_signature(pub_key, manipulated_msg, (r_val, s_val))
# print(f"Signature valid with fake message? {is_valid_manipulated}\n")

# # 5. Encrypt a FiniteFieldElement using elliptic curves
# plaintext_value = 123456789012345678901234567890  # Some large integer
# plaintext_field = FiniteFieldElement(plaintext_value, N)
# eph_pub_key, encrypted_field = encrypt_message(pub_key, plaintext_field, return_class=False)
# print(f"Original FiniteFieldElement: {plaintext_field}")
# print(f"Encrypted FiniteFieldElement: {encrypted_field}")
# print(f"Ephemeral Public Key: X={eph_pub_key.x.value}, Y={eph_pub_key.y.value}\n")

# # 6. Decrypt the FiniteFieldElement
# decrypted_field = decrypt_message(priv_key, eph_pub_key, encrypted_field)
# print(f"Decrypted FiniteFieldElement: {decrypted_field}")
# print(f"Decryption successful: {decrypted_field.value == plaintext_field.value}\n")

# # 7. Test homomorphic properties of the encryption scheme

# val_x = FiniteFieldElement(10, N)
# val_y = FiniteFieldElement(20, N)
# val_z = FiniteFieldElement(200, N)

# enc_x = encrypt_message(pub_key, val_x)
# enc_y = encrypt_message(pub_key, val_y,  enc_x.ephemeral_public_key, enc_x.shared_secret)
# enc_z = encrypt_message(pub_key, val_z,enc_x.ephemeral_public_key, enc_x.shared_secret  )

# # multiply by scalar
# enc_result = (enc_x *20)
# dec_result = enc_result.decrypt(priv_key)

# dec_val_z = enc_z.decrypt(priv_key)

# print(f"Decrypted Val Z of (x * y): {dec_val_z}")
# print(f"Decrypted Result of (x * y): {dec_result}")

# # multiply by scalar
# enc_result = (enc_x + enc_y)
# dec_result = enc_result.decrypt(priv_key)

# dec_val_z = enc_z.decrypt(priv_key)

# print(f"Decrypted Val Z of (x * y): {dec_val_z}")
# print(f"Decrypted Result of (x * y): {dec_result}")