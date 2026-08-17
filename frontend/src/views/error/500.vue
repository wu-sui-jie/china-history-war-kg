<!--
  500 服务器错误页面
  
  功能: 显示500服务器内部错误
    - 后端服务器发生错误时触发
    - 提供刷新和返回按钮
-->
<template>
    <lay-card class="error-page">
        <lay-exception status="500" title="500" describe="抱歉，服务器出错了">
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