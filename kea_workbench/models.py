from pydantic import BaseModel, Field, model_validator

PROJECTS = {
    "com.ugreen.iot": {"id": "69684909ee73c95fe1643567", "name": "APP-绿联声学"},
    "com.ugreen.iot.connect": {
        "id": "696eded40e4637c8b71e22a1",
        "name": "APP-绿联智联",
    },
}
ACTIVE = {"preparing", "running", "stopping", "finalizing"}
LABELS = {
    "preparing": "准备中",
    "running": "运行中",
    "stopping": "正在停止",
    "finalizing": "生成报告",
    "completed": "已完成",
    "cancelled": "已取消",
    "interrupted": "异常终止",
}


class RunConfig(BaseModel):
    serial: str = Field(min_length=1, max_length=120, pattern=r"^[\w.:-]+$")
    package: str = Field(default="com.ugreen.iot", pattern=r"^[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+$")
    runningMinutes: int = Field(default=600, ge=1, le=10080)
    maxStep: int = Field(default=99999, ge=1, le=2147483647)
    throttle: int = Field(default=300, ge=0, le=60000)
    takeScreenshots: bool = True
    preFailureScreenshots: int = Field(default=20, ge=0, le=1000)
    postFailureScreenshots: int = Field(default=5, ge=0, le=1000)
    profilePeriod: int = Field(default=25, ge=1, le=10000)
    platformEnabled: bool = True

    @model_validator(mode="after")
    def check(self):
        if self.takeScreenshots and self.postFailureScreenshots > self.preFailureScreenshots:
            raise ValueError("失败后截图数量不能超过失败前截图数量")
        if self.platformEnabled and self.package not in PROJECTS:
            raise ValueError("该 App 未配置项目关联，请关闭平台上报后运行")
        return self
