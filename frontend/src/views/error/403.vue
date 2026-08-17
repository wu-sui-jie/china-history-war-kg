<!--
  403 禁止访问错误页面
  
  功能: 显示403禁止访问错误
    - 用户没有权限访问特定页面时触发
    - 提供刷新和返回按钮
-->
<template>
    <lay-card class="error-page">
        <lay-exception status="403" title="403" describe="抱歉，你无权访问该页面">
            <template #extra>
                <lay-button>刷新</lay-button>
                <lay-button type="primary">返回</lay-button>
            </template>
        </lay-exception>
    </lay-card>
</template>

<style>
.error-page {
  padding-top: 200px;
  padding-bottom: 200px;
  margin: 10px;
}
</style>