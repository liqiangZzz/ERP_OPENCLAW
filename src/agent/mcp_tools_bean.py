# =============================================================================
# ★ MCP 工具相关的 Pydantic 数据模型（Bean）—— 供应商/零配件/订单参数校验
# =============================================================================
"""
MCP 工具相关的 Pydantic 数据模型（Bean）。

定义供应商查询、零配件搜索、采购订单等业务操作中使用的
请求参数模型和分组常量。供 MCP 工具调用时做参数校验和类型提示。
"""
from datetime import date
from decimal import Decimal
from typing import Optional, List

from pydantic import BaseModel, Field

# =============================================================================
# ★ 1. 分组名称常量 —— MCP 工具返回结果的分类标识
# =============================================================================

# 分组名称常量（用于 MCP 工具返回结果的分类标识）
GROUP_PART = "part"
GROUP_SUPPLIER = "supplier"


# =============================================================================
# ★ 2. 供应商与零配件查询参数模型
# =============================================================================
class SupplierQueryInput(BaseModel):
    """供应商查询参数模型"""
    name: str = Field(..., description="供应商名称（模糊查询），必填")


class PartSearchInput(BaseModel):
    """零配件搜素参数模型"""
    name: str = Field(..., description="零件名称（模糊查询），必填")


class PartQueryInput(BaseModel):
    """采购零部件查询参数模型"""
    current: Optional[int] = Field(1, description="当前页码，从1开始")
    size: Optional[int] = Field(10, description="每页大小")
    name: Optional[str] = Field(None, description="零件名称（模糊查询）,可以不传，则搜索所有采购零部件")
    category: Optional[str] = Field(None, description="分类(发动机类/车架类/电气类/制动类/传动类/外观件)")
    supplierId: Optional[int] = Field(
        None,
        description="供应商ID，根据供应商查询采购零件列表，可以先根据供应商的名字查询出供应商ID。"
    )


# =============================================================================
# ★ 3. 采购订单相关类型 —— 订单明细、创建/更新请求体
# =============================================================================

class OrderDetailItem(BaseModel):
    """采购订单明细项"""
    partId: int = Field(..., description="零部件Id，必填")
    quantity: int = Field(..., description="采购数量，必填，最小值为1")
    unitPrice: Decimal = Field(..., description="单价，必填")
    subtotal: Optional[Decimal] = Field(None, description="小计金额，不传则自动计算 = quantity * unitPrice")
    remark: Optional[str] = Field(None, description="明细备注")


class OrderInput(BaseModel):
    """采购订单请求模型（创建和更新公用）"""

    orderNumber: Optional[str] = Field(
        None,
        description="订单编号（唯一标识，不可重复）。规则：PO+年月日(8位)+3位随机数字。不传则自动生成"
    )
    totalAmount: Optional[Decimal] = Field(None, description="订单总金额，不传则自动根据明细进行计算")
    status: Optional[int] = Field(
        None,
        description="订单状态（1-待审批，2-已审批，3-采购中，4-已入库，5-已取消），默认1"
    )
    orderTime: Optional[str] = Field(
        None,
        description="下单时间，格式：yyyy-MM-ddTHH:mm:ss.SSS。不传则默认当前时间"
    )
    expectedDeliveryDate: Optional[date] = Field(None, description="预计交货日期，格式：yyyy-MM-dd")
    createdBy: Optional[int] = Field(None, description="创建人 Id")
    remark: Optional[str] = Field(None, description="备注")
    orderDetail: Optional[List[OrderDetailItem]] = Field(
        None,
        description="订单明细列表（创建时间必填，更新时间可选）。每项需提供 partId、quantity、unitPrice"
    )


# =============================================================================
# ★ 4. 订单搜索参数模型
# =============================================================================

class OrderSearchInput(BaseModel):
    """采购订单明细搜索参数模型"""
    partName: Optional[str] = Field(None, description="零部件名称（模糊查询）")
    startDate: Optional[str] = Field(None, description="开始日期（yyyy-MM-dd 格式）")
    endDate: Optional[str] = Field(None, description="结束日期（yyyy-MM-dd 格式）")
