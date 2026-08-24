from __future__ import annotations

from collections.abc import Sequence

import torch
from torch import Tensor, nn

# 세 가지 WiFi CSI 시계열 분류 모델

# 입력: [batch, time, subcarrier]
# 출력: [batch, num_classes]의 분류 logit



MODEL_NAMES = ("cnn", "cnn_bilstm_attention", "tcn_attention")


def _validate_input(x: Tensor, expected_subcarriers: int) -> None:
    if x.ndim != 3:
        raise ValueError(
            "입력은 [batch, time, subcarrier]의 3차원 Tensor여야 합니다. "
            f"현재 형상: {tuple(x.shape)}"
        )
    if x.shape[-1] != expected_subcarriers:
        raise ValueError(
            f"모델은 subcarrier={expected_subcarriers}를 기대하지만 "
            f"입력은 {x.shape[-1]}입니다."
        )


class TemporalAttentionPooling(nn.Module):
    """각 시간 특징의 중요도를 학습하는 additive attention pooling."""

    def __init__(self, feature_dim: int, attention_dim: int) -> None:
        super().__init__()
        self.score = nn.Sequential(
            nn.Linear(feature_dim, attention_dim),
            nn.Tanh(),
            nn.Linear(attention_dim, 1, bias=False),
        )

    def forward(self, sequence: Tensor) -> tuple[Tensor, Tensor]:
        if sequence.ndim != 3:
            raise ValueError("Attention 입력은 [batch, time, feature]여야 합니다.")
        scores = self.score(sequence).squeeze(-1)
        weights = torch.softmax(scores, dim=1)
        context = torch.sum(sequence * weights.unsqueeze(-1), dim=1)
        return context, weights


class PlainCNN1D(nn.Module):

    def __init__(
        self,
        num_subcarriers: int,
        num_classes: int,
        dropout: float = 0.30,
    ) -> None:
        super().__init__()
        self.num_subcarriers = num_subcarriers
        self.features = nn.Sequential(
            nn.Conv1d(num_subcarriers, 64, kernel_size=7, padding=3, bias=False),
            nn.BatchNorm1d(64),
            nn.GELU(),
            nn.MaxPool1d(kernel_size=2),
            nn.Conv1d(64, 128, kernel_size=5, padding=2, bias=False),
            nn.BatchNorm1d(128),
            nn.GELU(),
            nn.MaxPool1d(kernel_size=2),
            nn.Conv1d(128, 256, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm1d(256),
            nn.GELU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(256, 128),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(128, num_classes),
        )

    def forward(self, x: Tensor, return_attention: bool = False) -> Tensor:
        del return_attention
        _validate_input(x, self.num_subcarriers)
        x = x.transpose(1, 2)  # [B, S, T]
        return self.classifier(self.features(x))


class CNNBiLSTMAttention(nn.Module):

    def __init__(
        self,
        num_subcarriers: int,
        num_classes: int,
        lstm_hidden: int = 128,
        attention_dim: int = 128,
        dropout: float = 0.30,
    ) -> None:
        super().__init__()
        self.num_subcarriers = num_subcarriers
        self.cnn = nn.Sequential(
            nn.Conv1d(num_subcarriers, 64, kernel_size=5, padding=2, bias=False),
            nn.BatchNorm1d(64),
            nn.GELU(),
            nn.Conv1d(64, 128, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm1d(128),
            nn.GELU(),
            nn.MaxPool1d(kernel_size=2),
            nn.Dropout(dropout * 0.5),
        )
        self.bilstm = nn.LSTM(
            input_size=128,
            hidden_size=lstm_hidden,
            num_layers=1,
            batch_first=True,
            bidirectional=True,
        )
        feature_dim = 2 * lstm_hidden
        self.attention = TemporalAttentionPooling(feature_dim, attention_dim)
        self.classifier = nn.Sequential(
            nn.LayerNorm(feature_dim),
            nn.Linear(feature_dim, 128),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(128, num_classes),
        )

    def forward(
        self,
        x: Tensor,
        return_attention: bool = False,
    ) -> Tensor | tuple[Tensor, Tensor]:
        _validate_input(x, self.num_subcarriers)
        x = self.cnn(x.transpose(1, 2)).transpose(1, 2)
        sequence, _ = self.bilstm(x)
        context, weights = self.attention(sequence)
        logits = self.classifier(context)
        if return_attention:
            return logits, weights
        return logits


class CausalConv1d(nn.Conv1d):

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        dilation: int,
        bias: bool = False,
    ) -> None:
        trim = (kernel_size - 1) * dilation
        super().__init__(
            in_channels,
            out_channels,
            kernel_size=kernel_size,
            dilation=dilation,
            padding=trim,
            bias=bias,
        )
        self.trim = trim

    def forward(self, x: Tensor) -> Tensor:
        y = super().forward(x)
        return y[..., :-self.trim] if self.trim > 0 else y


class TemporalResidualBlock(nn.Module):

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        dilation: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.main = nn.Sequential(
            CausalConv1d(
                in_channels,
                out_channels,
                kernel_size=kernel_size,
                dilation=dilation,
            ),
            nn.BatchNorm1d(out_channels),
            nn.GELU(),
            nn.Dropout(dropout),
            CausalConv1d(
                out_channels,
                out_channels,
                kernel_size=kernel_size,
                dilation=dilation,
            ),
            nn.BatchNorm1d(out_channels),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.residual = (
            nn.Identity()
            if in_channels == out_channels
            else nn.Conv1d(in_channels, out_channels, kernel_size=1, bias=False)
        )
        self.activation = nn.GELU()

    def forward(self, x: Tensor) -> Tensor:
        return self.activation(self.main(x) + self.residual(x))


class TCNAttention(nn.Module):

    def __init__(
        self,
        num_subcarriers: int,
        num_classes: int,
        channels: Sequence[int] = (64, 64, 128, 128, 256),
        kernel_size: int = 3,
        attention_dim: int = 128,
        dropout: float = 0.20,
    ) -> None:
        super().__init__()
        if not channels:
            raise ValueError("TCN channels는 하나 이상이어야 합니다.")
        self.num_subcarriers = num_subcarriers

        blocks: list[nn.Module] = []
        in_channels = num_subcarriers
        for level, out_channels in enumerate(channels):
            blocks.append(
                TemporalResidualBlock(
                    in_channels=in_channels,
                    out_channels=out_channels,
                    kernel_size=kernel_size,
                    dilation=2**level,
                    dropout=dropout,
                )
            )
            in_channels = out_channels
        self.tcn = nn.Sequential(*blocks)
        self.attention = TemporalAttentionPooling(in_channels, attention_dim)
        self.classifier = nn.Sequential(
            nn.LayerNorm(in_channels),
            nn.Linear(in_channels, 128),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(128, num_classes),
        )

    def forward(
        self,
        x: Tensor,
        return_attention: bool = False,
    ) -> Tensor | tuple[Tensor, Tensor]:
        _validate_input(x, self.num_subcarriers)
        sequence = self.tcn(x.transpose(1, 2)).transpose(1, 2)
        context, weights = self.attention(sequence)
        logits = self.classifier(context)
        if return_attention:
            return logits, weights
        return logits


def build_model(
    name: str,
    num_subcarriers: int,
    num_classes: int,
) -> nn.Module:

    normalized = name.lower().replace("-", "_")
    if normalized == "cnn":
        return PlainCNN1D(num_subcarriers, num_classes)
    if normalized == "cnn_bilstm_attention":
        return CNNBiLSTMAttention(num_subcarriers, num_classes)
    if normalized == "tcn_attention":
        return TCNAttention(num_subcarriers, num_classes)
    raise ValueError(f"지원하지 않는 모델: {name}. 선택 가능: {MODEL_NAMES}")


def count_trainable_parameters(model: nn.Module) -> int:
    return sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
