from utils import encrypt_message, decrypt_message, generate_key_pair, sign_message

from utils import EncryptedFiniteFieldElement, FiniteFieldElement, Point, PolynomialPoint, encrypt_message, decrypt_message, lagrange_interpolation, sign_message, get_shares, N
from server import generate_key_pair
import unittest

class TestUtils(unittest.TestCase):
    
    def test_polynomial_evaluation_and_interpolation(self):
        # Test parameters
        secret_value = 12345
        n_shares = 5
        k_threshold = 3
        shares = get_shares(secret_value, n_shares, k_threshold)
        
        # Select k shares for interpolation
        selected_shares = shares[:k_threshold]
        x_values = [share.x for share in selected_shares]
        y_values = [share.y for share in selected_shares]
        
        # Interpolate to find the secret
        reconstructed_secret = lagrange_interpolation(selected_shares, FiniteFieldElement(0, N))
        self.assertEqual(reconstructed_secret.value, secret_value)
        
    def test_get_shares_and_encryption(self):
        secret_value = 67890
        n_shares = 4
        k_threshold = 2
        shares = get_shares(secret_value, n_shares, k_threshold)
        
        private_key, public_key = generate_key_pair()
        
        for share in shares:
            x_encrypted = encrypt_message(public_key, share.x)
            y_encrypted = encrypt_message(public_key, share.y)
            
            json_data = {
                'x': x_encrypted.serialize(),
                'y': y_encrypted.serialize()
            }
            
            x_decrypted = EncryptedFiniteFieldElement(None,None).deserialize(json_data['x']).decrypt(private_key)
            y_decrypted = EncryptedFiniteFieldElement(None,None).deserialize(json_data['y']).decrypt(private_key)
            
            self.assertEqual(x_decrypted.value, share.x.value)
            self.assertEqual(y_decrypted.value, share.y.value)
            
    def test_encrypt_affine_transform(self):
        
        x = FiniteFieldElement(10, N)
        intercept = FiniteFieldElement(20, N)
        coef = FiniteFieldElement(30, N)
        
        private_key, public_key = generate_key_pair()
        
        x_encrypted = encrypt_message(public_key, x)
        intercept_encrypted = encrypt_message(public_key, intercept)
        coef_encrypted = encrypt_message(public_key, coef)
        
        affine_transform_encrypted = x_encrypted**coef * intercept_encrypted
        
        affine_transform_decrypted = affine_transform_encrypted.decrypt(private_key)
        
        self.assertEqual(affine_transform_decrypted.value, (x.value * coef.value + intercept.value) % N)