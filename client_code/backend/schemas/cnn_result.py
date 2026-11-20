"""exp3 추론 후처리 결과 DTO. AI 라이브러리에 의존하지 않는다."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Color = Literal['Black', 'Blue', 'Brown', 'Green', 'Grey', 'Orange', 'Pink',
                'Purple', 'Red', 'White', 'Yellow']
Carrying = Literal['Backpack', 'MessengerBag', 'PlasticBag', 'Luggage', 'Other']


class CNNAttributes(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)

    gender: Literal['Male', 'Female']
    age: Literal['소년', '소녀', '청년', '장년']
    hat: Literal['No Hat', 'Hat']
    upper_color: list[Color] = Field(min_length=1, max_length=11)
    lower_color: list[Color] = Field(min_length=1, max_length=1)
    sleeve: Literal['Long Sleeve', 'Short Sleeve', 'No Sleeve']
    lower_type: Literal['Long Pants', 'Short Pants', 'Skirt']
    carrying: list[Carrying] = Field(max_length=5)

    @model_validator(mode='after')
    def consistent_labels(self):
        if ((self.age == '소년' and self.gender != 'Male')
                or (self.age == '소녀' and self.gender != 'Female')):
            raise ValueError('소년/소녀와 성별 예측이 일치해야 합니다.')
        for name in ('upper_color', 'lower_color', 'carrying'):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError('중복된 속성 라벨은 허용하지 않습니다.')
        return self


class CNNResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)

    model_version: Literal['exp3']
    attributes: CNNAttributes


class EventCNNResultUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)

    cnn_result: CNNResult
