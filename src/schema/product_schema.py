"""商品上架管理模块的请求模型。

商品的基础字段是「商品id、名称、价格、库存、上架状态」，持久化仍复用
``schema.live_commerce_schema.ProductItem``（它已经包含这些字段以及描述、
规格、图片等扩展字段）。这里只定义 HTTP 接口的入参，避免把客户端可写的
字段和内部字段（如 soldCount / rating）混在一起。
"""

from pydantic import BaseModel, Field


class ProductCreateRequest(BaseModel):
    """新增商品。

    ``productId`` 留空时由服务端生成（P001、P002 …）。其余扩展字段都有
    默认值，主播只传基础字段也能创建成功。
    """

    productId: str | None = Field(
        default=None, max_length=64, description="商品ID；留空由服务端生成"
    )
    name: str = Field(..., min_length=1, max_length=100, description="商品名称")
    price: float = Field(..., gt=0, description="售卖价格（元）")
    stock: int = Field(..., ge=0, description="库存数量")
    isOnSale: bool = Field(default=True, description="上架状态：True=已上架")

    # ---- 可选扩展字段（与 ProductItem 对齐）----
    originalPrice: float | None = Field(default=None, gt=0, description="原价；缺省等于 price")
    category: str = Field(default="", description="品类")
    description: str = Field(default="", description="商品描述")
    specs: list[str] = Field(default_factory=list, description="规格列表，每项形如 '键: 值'")
    imageUrl: str = Field(default="", description="商品主图 URL")
    tags: list[str] = Field(default_factory=list, description="商品标签")
    sellingPoints: list[str] = Field(default_factory=list, description="独立卖点列表")
    isInLibrary: bool = Field(default=True, description="是否在备选商品库中")


class ProductUpdateRequest(BaseModel):
    """修改商品。所有字段可选，只更新请求里出现的字段。

    ``productId`` 不允许修改 —— 它是订单、评价、讲解状态等数据的关联键。
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    price: float | None = Field(default=None, gt=0)
    stock: int | None = Field(default=None, ge=0)
    isOnSale: bool | None = Field(default=None, description="上架状态；True=上架，False=下架")

    originalPrice: float | None = Field(default=None, gt=0)
    category: str | None = None
    description: str | None = None
    specs: list[str] | None = None
    imageUrl: str | None = None
    tags: list[str] | None = None
    sellingPoints: list[str] | None = None
    isInLibrary: bool | None = None


class ProductOnSaleRequest(BaseModel):
    """上下架商品。"""

    onSale: bool = Field(..., description="True=上架，False=下架")
