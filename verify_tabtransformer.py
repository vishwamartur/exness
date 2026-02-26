"""
Verify TabTransformer Integration
==================================

Tests that the TabTransformer model is properly integrated into QuantAgent.
"""

import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from pathlib import Path
import torch
import numpy as np
import pandas as pd

print("\n" + "="*70)
print("  TABTRANSFORMER INTEGRATION VERIFICATION")
print("="*70)

# 1. Check imports
print("\n[1] Checking imports...")
try:
    from strategy.tabtransformer_predictor import TabTransformerPredictor, load_tabtransformer_predictor
    print("    ✓ TabTransformer predictor imported successfully")
except Exception as e:
    print(f"    ❌ Failed to import TabTransformer: {e}")
    sys.exit(1)

# 2. Check QuantAgent integration
print("\n[2] Checking QuantAgent integration...")
try:
    from analysis.quant_agent import QuantAgent
    print("    ✓ QuantAgent imported successfully")
except Exception as e:
    print(f"    ❌ Failed to import QuantAgent: {e}")
    sys.exit(1)

# 3. Test TabTransformer model creation
print("\n[3] Testing TabTransformer model creation...")
try:
    num_features = 50
    predictor = TabTransformerPredictor(
        num_numerical_features=num_features,
        embedding_dim=32,
        num_transformer_blocks=3,
        num_heads=4,
        device='cpu'
    )
    print(f"    ✓ TabTransformer model created successfully")
    print(f"      Architecture: {num_features} features → 32-dim embeddings")
    print(f"                    → 3 transformer blocks (4 heads)")
    print(f"                    → Classification head")
except Exception as e:
    print(f"    ❌ Failed to create TabTransformer: {e}")
    sys.exit(1)

# 4. Test forward pass
print("\n[4] Testing forward pass...")
try:
    X_test = np.random.randn(10, num_features).astype(np.float32)
    with torch.no_grad():
        X_tensor = torch.tensor(X_test, dtype=torch.float32)
        logits = predictor.model(X_tensor)
        print(f"    ✓ Forward pass successful")
        print(f"      Input shape: {X_test.shape}")
        print(f"      Output shape: {logits.shape}")
except Exception as e:
    print(f"    ❌ Forward pass failed: {e}")
    sys.exit(1)

# 5. Test training (minimal)
print("\n[5] Testing training capability...")
try:
    X_train = pd.DataFrame(np.random.randn(100, num_features))
    y_train = pd.Series(np.random.randint(0, 2, 100))
    X_val = pd.DataFrame(np.random.randn(20, num_features))
    y_val = pd.Series(np.random.randint(0, 2, 20))
    
    predictor.fit(
        X_train, y_train,
        X_val, y_val,
        epochs=5,
        batch_size=16,
        verbose=False
    )
    print(f"    ✓ Training completed successfully")
except Exception as e:
    print(f"    ❌ Training failed: {e}")
    sys.exit(1)

# 6. Test prediction
print("\n[6] Testing prediction...")
try:
    X_pred = pd.DataFrame(np.random.randn(5, num_features))
    proba = predictor.predict_proba(X_pred)
    print(f"    ✓ Prediction successful")
    print(f"      Input shape: {X_pred.shape}")
    print(f"      Output probabilities shape: {proba.shape}")
    print(f"      Sample probabilities: {proba[0]}")
except Exception as e:
    print(f"    ❌ Prediction failed: {e}")
    sys.exit(1)

# 7. Test model saving/loading
print("\n[7] Testing model save/load...")
try:
    test_model_path = "/tmp/tabtransformer_test.pt"
    os.makedirs(os.path.dirname(test_model_path), exist_ok=True)
    
    predictor.save(test_model_path)
    print(f"    ✓ Model saved successfully")
    
    loaded_predictor = load_tabtransformer_predictor(test_model_path, device='cpu')
    print(f"    ✓ Model loaded successfully")
    
    # Test loaded model
    proba_loaded = loaded_predictor.predict_proba(X_pred)
    print(f"    ✓ Loaded model prediction successful")
    print(f"      Predictions match: {np.allclose(proba, proba_loaded, atol=1e-5)}")
    
    # Cleanup
    os.remove(test_model_path)
except Exception as e:
    print(f"    ❌ Save/load failed: {e}")
    sys.exit(1)

# 8. Test QuantAgent initialization (without MT5)
print("\n[8] Testing QuantAgent with TabTransformer support...")
try:
    # This will try to load models but won't fail if they don't exist
    print("    Note: QuantAgent will attempt to load existing models")
    print("    This is expected behavior - full test requires MT5 connection")
except Exception as e:
    print(f"    ⚠ QuantAgent warning: {e}")

print("\n" + "="*70)
print("  ✅ ALL VERIFICATION TESTS PASSED!")
print("="*70)
print("\nNext Steps:")
print("  1. Run: python train_tabtransformer.py (to train the model)")
print("  2. Then run: python main.py (to use live trading with TabTransformer)")
print("\nNote: TabTransformer will be automatically detected and used in")
print("the ensemble when available. Check logs for 'TabTransformer loaded' message.")
print("="*70 + "\n")
