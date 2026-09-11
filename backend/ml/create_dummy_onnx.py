import torch
import torch.nn as nn
import os
import sys

# Force UTF-8 output to avoid PyTorch logging emojis crashing on Windows cp1252
sys.stdout.reconfigure(encoding='utf-8')

class DummyUNet(nn.Module):
    def __init__(self):
        super(DummyUNet, self).__init__()
        # 4 input bands (e.g. Sentinel-2 Blue, Green, Red, NIR), 1 output mask (flood vs no-flood)
        self.conv = nn.Conv2d(4, 1, kernel_size=3, padding=1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        # A real U-Net would have enc/dec layers. We just do a 1-layer transform for the prototype scaffold.
        return self.sigmoid(self.conv(x))

def create_model():
    model = DummyUNet()
    model.eval()
    
    # Dummy input tensor [batch_size, channels, height, width]
    dummy_input = torch.randn(1, 4, 256, 256)
    
    # Handle being run from inside backend/ml or from repo root
    save_dir = "."
    if not os.path.exists("ml") and os.path.basename(os.getcwd()) != "ml":
        save_dir = "backend/ml"
    else:
        save_dir = "ml" if os.path.basename(os.getcwd()) == "backend" else "."
        
    os.makedirs(save_dir, exist_ok=True)
    onnx_path = os.path.join(save_dir, "flood_unet.onnx")
    
    torch.onnx.export(
        model, 
        dummy_input, 
        onnx_path,
        export_params=True,
        opset_version=11,
        do_constant_folding=True,
        input_names=['input'],
        output_names=['output'],
        dynamic_axes={'input': {0: 'batch_size', 2: 'height', 3: 'width'},
                      'output': {0: 'batch_size', 2: 'height', 3: 'width'}}
    )
    print(f"Dummy ONNX model exported to {onnx_path}")

if __name__ == "__main__":
    create_model()
