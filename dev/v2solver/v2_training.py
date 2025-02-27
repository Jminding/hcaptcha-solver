import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torch.utils.data as utils
from torch.nn import init

from datetime import datetime as dt
import os
import pandas as pd
from copy import deepcopy as copy

from PIL import Image

IMAGES_DIR_V2 = "../../data/images/v2/"
CLICKABLE_AREA_BOUNDARIES = (83,194,417,527)
CLICKABLE_AREA_SIZE = (CLICKABLE_AREA_BOUNDARIES[2] - CLICKABLE_AREA_BOUNDARIES[0], CLICKABLE_AREA_BOUNDARIES[3] - CLICKABLE_AREA_BOUNDARIES[1])
  
class EuclideanDistanceLoss(nn.Module):  
    def __init__(self):  
        super(EuclideanDistanceLoss, self).__init__()  
  
    def forward(self, output, target, verbose=False):  
        assert output.size() == target.size()  
  
        squared_diff = (output - target) ** 2  
  
        sum_squared_diff = torch.sum(squared_diff, dim=1)  
        euclidean_distance = sum_squared_diff
        scaled_distance = torch.where(euclidean_distance < 0.025, euclidean_distance*0.2, (5*euclidean_distance) + 1)

        if verbose : print(f"correct: {len(scaled_distance[scaled_distance < 1])} / {len(scaled_distance)}")
    
        loss = torch.mean(scaled_distance)  
  
        return loss  

class Model_Training:
    def __init__(self):
        self.batch_size = 16
        self.lr = 0.0001
        self.log_interval = 1

        def calculate_linear_input_size(model, input_shape):  
            dummy_input = torch.zeros(input_shape)  
            output = model(dummy_input)  
            return output.view(output.size(0), -1).size(1)  
        
        # Define your convolutional layers  
        conv_layers = nn.Sequential(  
            nn.Conv2d(3, 8, kernel_size=7, padding=3),  
            # nn.BatchNorm2d(8),  
            nn.ReLU(),  
            nn.MaxPool2d(2, stride=2),  
            nn.Conv2d(8, 16, kernel_size=5, padding=2),  
            # nn.BatchNorm2d(16),  
            nn.ReLU(),  
            nn.MaxPool2d(2, stride=2),  
            nn.Conv2d(16, 32, kernel_size=3, padding=1),  
            # nn.BatchNorm2d(32),  
            nn.ReLU(),  
            nn.MaxPool2d(2, stride=2), 
        )  
         
        self.model = nn.Sequential(  
            conv_layers,  
            nn.Flatten(),  
            # nn.Dropout(0.5),
            nn.Linear(53792, 512),  
            nn.ReLU(),  
            # nn.Dropout(0.5),  
            nn.Linear(512, 64),  
            nn.ReLU(),  
            nn.Linear(64, 2),  
            nn.Sigmoid()  
        )  
        
        # Initialize weights using Xavier/Glorot initialization  
        for layer in self.model:  
            if isinstance(layer, nn.Linear) or isinstance(layer, nn.Conv2d):  
                init.xavier_uniform_(layer.weight)  
            elif isinstance(layer, nn.BatchNorm2d):  
                init.constant_(layer.weight, 1)  
                init.constant_(layer.bias, 0)  
        
        self.optimizer = optim.Adam(self.model.parameters(), lr=self.lr)  

        self.criterion = EuclideanDistanceLoss()

    def train(self, db2, epochs=10, verbose=True):
        x, y = get_image_data(db2)
        print("Sample 0:\n", x[0], "\n", y[0])
        train_loader, test_loader = self.data_to_loader(x, y)
        self.model = self.training_loop(train_loader, test_loader, verbose=verbose, epochs=epochs)
        return self.model

    def data_to_loader(self, x, y, test_split=0.25):
        assert len(x) == len(y), "x and y must have the same length"
        
        x = torch.from_numpy(x).float()
        y = torch.from_numpy(y).float()

        dataset = utils.TensorDataset(x, y)
        test_size = int(len(dataset) * test_split)
        train_size = len(dataset) - test_size

        overhang = train_size % self.batch_size
        train_size -= overhang
        test_size += overhang

        train_dataset, test_dataset = torch.utils.data.random_split(dataset, [train_size, test_size])

        print(f"train size: {len(train_dataset)}, test size: {len(test_dataset)}")


        train_loader = utils.DataLoader(train_dataset, batch_size=self.batch_size, drop_last=True, shuffle=True)
        test_loader = utils.DataLoader(test_dataset, batch_size=test_size, drop_last=True, shuffle=False)

        print("single element shape:", train_loader.dataset[0][0].shape)

        return train_loader, test_loader

    def training_loop(self, train_loader, test_loader, verbose=True, epochs=50):
        losses = []
        for epoch in range(1, epochs + 1):
            # training
            self.model.train()
            train_loss = 0
            for batch_idx, (data, target) in enumerate(train_loader):
                self.optimizer.zero_grad()
                output = self.model(data)
                loss = self.criterion(output, target)
                loss.backward()
                train_loss += loss.item()
                self.optimizer.step()
            train_loss /= len(train_loader)

            # testing
            self.model.eval()
            test_loss = 0
            with torch.no_grad():
                for i, (data, target) in enumerate(test_loader):
                    output = self.model(data)
                    test_loss += self.criterion(output, target, verbose=True).item()

            if verbose : print(f'Epoch: {epoch}, Train Loss: {train_loss:.4f} Test Loss: {test_loss:.4f}'); print(output[0], target[0])

        return self.model

    def predict_pil(self, pil_images):
        if not isinstance(pil_images, list):
            pil_images = [pil_images]
        preprocessed_images = preprocess_pil(pil_images)
        predictions = self.predict(preprocessed_images)
        print(predictions)
        return postprocess_positions(predictions)

    def predict(self, x):
        x = torch.from_numpy(x).float()
        with torch.no_grad():
            output = self.model(x)
            return output.detach().numpy()


class Loaded_Model:
    def __init__(self, model_path):
        self.model = torch.load(model_path)
        self.model.eval()
    
    def predict(self, x):
        x = torch.from_numpy(x).float()
        with torch.no_grad():
            output = self.model(x)
            return output.round().detach().numpy()

def get_image_data(db2):
    image_paths, positions = db2.get_solved_captchas(count=1000)

    images_raw = []
    positions_raw = []
    for i in range(len(image_paths)):
        try:
            img = Image.open(open(IMAGES_DIR_V2 + image_paths[i], 'rb'))
            img.crop((0,0,0,0))
            images_raw.append(img)
            positions_raw.append(positions[i])
        except Exception as e:
            print(e)
            print(f"Could not load image: {image_paths[i]}")
    print(len(images_raw))
    print(images_raw[0].size)

    useable_indexes = [i for i in range(len(images_raw)) if images_raw[i].size == (500, 536)]

    useable_images = [images_raw[i] for i in useable_indexes]
    positions_raw = [positions_raw[i] for i in useable_indexes]
    print(f"Found {len(useable_images)} useable images")

    return preprocess_pil(useable_images), preprocess_positions(positions_raw)

def preprocess_pil(images):
    if not isinstance(images, list):
        images = [images]
    images = [image.crop(CLICKABLE_AREA_BOUNDARIES) for image in images]
    x = np.asarray(images)
    x = x / 255 # norming
    x = np.moveaxis(x, [1,2,3], [2,3,1]) # color channel first
    x = x[:,:-1,:,:] # remove alpha channel
    print(f"x shape: {x.shape}")
    return x

def postprocess_pil(images):
    if len(images.shape) == 3:
        images = np.expand_dims(images, axis=0)
    images *= 255
    images = np.moveaxis(images, [1], [-1]) # color channel last
    images = images.astype(np.uint8)
    return images  

def preprocess_positions(positions):
    y = np.asarray(positions)
    y = y / CLICKABLE_AREA_SIZE # norming
    y[:,1] = 1 - y[:,1]
    print(f"y shape: {y.shape}")
    print(f"pos: {positions[0]}")
    print(f"y: {y[0]}")
    return y

def postprocess_positions(positions):
    positions[:,1] = 1 - positions[:,1]
    scaled = positions * CLICKABLE_AREA_SIZE
    return scaled