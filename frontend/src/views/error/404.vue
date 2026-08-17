<!--
  404 页面不存在错误页面
  
  功能: 显示404页面不存在错误
    - 访问的路由不存在时触发
    - 提供刷新和返回按钮
-->
<template>
    <lay-card class="error-page">
        <lay-exception status="404" title="404" describe="抱歉，你访问的页面不存在">
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